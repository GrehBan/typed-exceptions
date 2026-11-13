"""Immutable snapshots of exception chains.

A snapshot records an exception and everything reachable from it (its
``__cause__``, its ``__context__`` and the members of an exception group) as
plain data. It can be stored, compared and serialized after the original
exceptions and the objects their fields pointed to are gone.
"""

import dataclasses
import json
import math
import traceback
from collections.abc import Iterator, Mapping
from typing import Any, Final

from typed_exceptions.template import _obj_name, _safe_str

MAX_EXCEPTIONS: Final[int] = 253
# Captured locals are ``repr`` strings and can be arbitrarily large.
MAX_LOCAL_LENGTH: Final[int] = 200
REDACTED_LOCAL: Final[str] = "<redacted>"
# Nesting deeper than this is stringified instead of walked, which also
# bounds self-referencing containers.
MAX_CONTEXT_DEPTH: Final[int] = 8


def _plain(value: object, depth: int = 0) -> Any:
    """Reduce ``value`` to JSON types, copying every container.

    A snapshot has to stay the same after the exception's fields change and
    after the objects they point to are gone, so values are converted at
    capture time instead of being kept alive. Anything that is not a plain
    scalar, mapping, sequence or set becomes its ``str``; tuples become
    lists and sets become sorted lists.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # NaN and infinity are not valid JSON.
        return value if math.isfinite(value) else _safe_str(value)
    if depth >= MAX_CONTEXT_DEPTH:
        return _safe_str(value)
    try:
        if isinstance(value, Mapping):
            return {
                (key if isinstance(key, str) else _safe_str(key)): _plain(
                    item, depth + 1
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [_plain(item, depth + 1) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(
                (_plain(item, depth + 1) for item in value), key=_safe_str
            )
    except Exception:
        # A container with a broken iterator must not break the snapshot.
        return _safe_str(value)
    return _safe_str(value)


def _typed_context(
    exc: BaseException,
) -> tuple[dict[str, Any] | None, tuple[str, ...] | None]:
    # Plain getattr instead of isinstance() against a runtime_checkable
    # Protocol: on 3.12+ the protocol check goes through getattr_static and
    # was the single most expensive step per exception.
    redact = getattr(exc, "__redact__", None)
    context = getattr(exc, "context", None)
    if redact is None or context is None:
        return None, None

    # Sorted: a frozenset iterates in hash order, which differs per run.
    return _plain(dict(context)), tuple(sorted(redact))


def _ref(e: BaseException | None, ids: dict[int, int]) -> int | None:
    return None if e is None else ids.get(id(e))


def _group_refs(e: BaseException, ids: dict[int, int]) -> tuple[int, ...]:
    if not isinstance(e, BaseExceptionGroup):
        return ()
    return tuple(
        i for sub in e.exceptions if (i := ids.get(id(sub))) is not None
    )


def _notes(e: BaseException) -> tuple[str, ...]:
    raw = getattr(e, "__notes__", None)
    # __notes__ is user-writable: a bare str would split into characters and
    # a non-str note would break JSON encoding.
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(n if isinstance(n, str) else _safe_str(n) for n in raw)


def _hidden_names(order: list[BaseException]) -> frozenset[str]:
    """Collect every redacted field name declared along the chain."""
    hidden: set[str] = set()
    for e in order:
        redact = getattr(e, "__redact__", None)
        if redact is not None:
            hidden.update(redact)
    return frozenset(hidden)


def _scrub_locals(
    values: dict[str, str] | None, hidden: frozenset[str]
) -> dict[str, str] | None:
    """Hide redacted names and clip long reprs in captured locals.

    Redaction removes a field from ``context``, but the same value is
    usually also a local variable of the frames it passed through. Matching
    by name is a heuristic: it catches the constructor argument and the
    obvious copies, not a secret that was renamed.
    """
    if values is None:
        return None
    return {
        name: REDACTED_LOCAL if name in hidden else _clip(text)
        for name, text in values.items()
    }


def _clip(text: str) -> str:
    if len(text) <= MAX_LOCAL_LENGTH:
        return text
    return text[: MAX_LOCAL_LENGTH - 1] + "…"


def _json_default(value: object) -> str:
    # Context values are arbitrary objects. Serializing a snapshot happens
    # while an error is being handled, so it must not raise a second one.
    return _safe_str(value)


def _children(e: BaseException) -> Iterator[BaseException]:
    if e.__cause__ is not None:
        yield e.__cause__
    if e.__context__ is not None:
        yield e.__context__
    if isinstance(e, BaseExceptionGroup):
        yield from e.exceptions


def _discover(
    root: BaseException, limit: int
) -> tuple[list[BaseException], dict[int, int], bool]:
    """Walk the chain breadth-first, numbering each exception once.

    ``order`` doubles as the work queue, so an exception reachable along
    several paths is queued once and no separate queue is kept. Stopping at
    ``limit`` keeps the first ``limit`` exceptions in discovery order, the
    same set a pop-time check would keep.

    Returns:
        The exceptions in order, their ids, and whether any reachable
        exception was left out because of ``limit``.
    """
    order: list[BaseException] = []
    ids: dict[int, int] = {}
    if limit < 1:
        return order, ids, True

    order.append(root)
    ids[id(root)] = 0
    cursor = 0
    while cursor < len(order):
        for child in _children(order[cursor]):
            identity = id(child)
            if identity in ids:
                continue
            if len(order) >= limit:
                return order, ids, True
            ids[identity] = len(order)
            order.append(child)
        cursor += 1
    return order, ids, False


def _snapshot(
    index: int,
    e: BaseException,
    ids: dict[int, int],
    capture_locals: bool,
    hidden: frozenset[str],
) -> ExceptionSnapshot:
    context, redacted = _typed_context(e)
    # lookup_lines=False: the source line is read once, in from_summary,
    # instead of twice (eagerly by extract(), then again by ``.line``).
    summary = traceback.StackSummary.extract(
        traceback.walk_tb(e.__traceback__),
        capture_locals=capture_locals,
        lookup_lines=False,
    )
    return ExceptionSnapshot(
        id=index,
        type=_obj_name(e),
        message=_safe_str(e),
        context=context,
        redacted=redacted,
        context_id=_ref(e.__context__, ids),
        cause_id=_ref(e.__cause__, ids),
        suppress_context=e.__suppress_context__,
        group_ids=_group_refs(e, ids),
        trace=tuple(FrameSnapshot.from_summary(f, hidden) for f in summary),
        notes=_notes(e),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class FrameSnapshot:
    """One traceback frame.

    Attributes:
        file_name: Source file of the frame.
        line_number: Line being executed, if known.
        name: Name of the executing function.
        line_text: Source text of that line, if it could be read.
        locals: ``repr`` of each local variable, or ``None`` unless locals
            were captured. Names redacted anywhere in the chain are replaced
            by ``"<redacted>"`` and long values are clipped to
            ``MAX_LOCAL_LENGTH`` characters.
    """

    file_name: str
    line_number: int | None
    name: str
    line_text: str | None
    locals: dict[str, str] | None

    @classmethod
    def from_summary(
        cls,
        f: traceback.FrameSummary,
        hidden: frozenset[str] = frozenset(),
    ) -> FrameSnapshot:
        """Build a frame snapshot from a ``traceback`` frame summary.

        Args:
            f: Frame summary to convert.
            hidden: Local variable names whose values must not be kept.

        Returns:
            The new frame snapshot.
        """
        return cls(
            f.filename,
            f.lineno,
            f.name,
            f.line,
            _scrub_locals(f.locals, hidden),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the frame as a JSON-compatible dictionary."""
        return {
            "file_name": self.file_name,
            "line_number": self.line_number,
            "name": self.name,
            "line_text": self.line_text,
            "locals": self.locals,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ExceptionSnapshot:
    """One exception of a chain.

    Exceptions refer to each other by ``id``, the position in
    ``ExceptionChain.items``, so shared and cyclic chains stay finite.

    Attributes:
        id: Position of this exception in the chain.
        type: Qualified name of the exception class.
        message: ``str()`` of the exception, or a placeholder if that fails.
        context: ``context`` of a ``TypedException`` reduced to JSON types,
            or ``None`` for any other exception.
        redacted: Sorted names of redacted fields, or ``None`` for an
            exception that is not a ``TypedException``.
        context_id: ``id`` of ``__context__``, or ``None`` if there is none
            or it was cut off by the limit.
        cause_id: ``id`` of ``__cause__``, with the same rules.
        suppress_context: Value of ``__suppress_context__``.
        group_ids: ``id`` of each exception-group member that was captured.
        trace: Traceback frames, outermost first.
        notes: Exception notes as strings.
    """

    # ``context`` holds JSON types only (see ``_plain``), never live objects.
    id: int
    type: str
    message: str
    context: dict[str, Any] | None
    redacted: tuple[str, ...] | None
    context_id: int | None
    cause_id: int | None
    suppress_context: bool
    group_ids: tuple[int, ...]
    trace: tuple[FrameSnapshot, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the exception as a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "type": self.type,
            "message": self.message,
            "context": self.context,
            "redacted": (
                None if self.redacted is None else list(self.redacted)
            ),
            "context_id": self.context_id,
            "cause_id": self.cause_id,
            "suppress_context": self.suppress_context,
            "group_ids": list(self.group_ids),
            "trace": [f.to_dict() for f in self.trace],
            "notes": list(self.notes),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ExceptionChain:
    """An exception and everything reachable from it.

    Attributes:
        items: Captured exceptions in breadth-first order; the exception the
            snapshot was taken of is first. Causes come before contexts.
        schema_version: Caller-supplied version of the serialized layout.
        truncated: Whether reachable exceptions were left out because of the
            limit.
    """

    items: tuple[ExceptionSnapshot, ...]
    schema_version: str
    truncated: bool = False

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        *,
        schema_version: str,
        capture_locals: bool = False,
        limit: int = MAX_EXCEPTIONS,
    ) -> ExceptionChain:
        """Capture ``exc`` and the exceptions linked to it.

        Args:
            exc: Exception to capture.
            schema_version: Version label stored in the result.
            capture_locals: Also record the local variables of every frame.
                Names of redacted fields are hidden and long values are
                clipped, but a secret under another name is kept.
            limit: Most exceptions to capture; the rest sets ``truncated``.

        Returns:
            The captured chain.
        """
        order, ids, truncated = _discover(exc, limit)
        hidden = _hidden_names(order) if capture_locals else frozenset()
        items = tuple(
            _snapshot(i, e, ids, capture_locals, hidden)
            for i, e in enumerate(order)
        )
        return cls(items, schema_version, truncated)

    def to_dict(self) -> dict[str, Any]:
        """Return the chain as a JSON-compatible dictionary.

        Returns:
            A mapping with ``root`` (always ``0``), ``exceptions``,
            ``schema_version`` and ``truncated``.
        """
        return {
            "root": 0,
            "exceptions": [s.to_dict() for s in self.items],
            "schema_version": self.schema_version,
            "truncated": self.truncated,
        }

    def to_json(self) -> str:
        """Return the chain as a JSON string.

        Returns:
            The JSON text. Values JSON cannot encode are stringified, so
            this does not raise on odd objects.
        """
        return json.dumps(
            self.to_dict(), ensure_ascii=False, default=_json_default
        )


def snapshot_exception(
    exc: BaseException,
    *,
    schema_version: str,
    capture_locals: bool = False,
    limit: int = MAX_EXCEPTIONS,
) -> ExceptionChain:
    """Capture an exception and its chain as an immutable snapshot.

    Works for any exception; ``TypedException`` instances also contribute
    their ``context`` and redacted field names.

    Args:
        exc: Exception to capture.
        schema_version: Version label stored in the result.
        capture_locals: Also record the local variables of every frame.
        limit: Most exceptions to capture.

    Returns:
        The captured chain.
    """
    return ExceptionChain.from_exception(
        exc=exc,
        schema_version=schema_version,
        capture_locals=capture_locals,
        limit=limit,
    )
