"""Typed, contextual exceptions with keyword-only fields for Python 3.14+.

Subclass fields are keyword-only. PEP 681 supports static checking,
while runtime enforces required fields and rejects unknown fields.
"""

from __future__ import annotations

import sys
from annotationlib import Format, get_annotations
from ast import literal_eval
from collections.abc import Callable, Mapping
from dataclasses import MISSING, Field, dataclass, field
from string import Formatter
from types import MappingProxyType, MemberDescriptorType, ModuleType
from typing import (
    Any,
    ClassVar,
    cast,
    dataclass_transform,
    get_origin,
)

__all__ = ["BetterException"]

type _ExceptionState = (
    dict[str, Any] | tuple[dict[str, Any] | None, dict[str, Any]] | None
)
type _FieldInitializer = tuple[str, Any, Callable[[], Any] | None]


def _declared_annotations(cls: type) -> dict[str, Any]:
    # PEP 649: __annotations__ can raise NameError for forward refs.
    return get_annotations(cls, format=Format.FORWARDREF)


@dataclass(frozen=True, slots=True)
class _FieldSpec:
    default: Any = MISSING
    factory: Callable[[], Any] | None = None
    redact: bool = False


def _is_classvar(annotation: object, owner: type) -> bool:
    if annotation is ClassVar or get_origin(annotation) is ClassVar:
        return True
    # PEP 563 / PEP 649 string annotations must not be evaluated: a forward
    # reference may not be resolvable while __init_subclass__ is running.
    if isinstance(annotation, str):
        if annotation.startswith(("'", '"')):
            try:
                unquoted: object = literal_eval(annotation)
            except SyntaxError, ValueError:
                return False
            if not isinstance(unquoted, str):
                return False
            annotation = unquoted
        name = "".join(annotation.split())
        head = name.partition("[")[0]
        parts = head.split(".")
        if not all(part.isidentifier() for part in parts):
            return False
        value = vars(owner).get(parts[0], MISSING)
        if value is MISSING:
            module = sys.modules.get(owner.__module__)
            if module is not None:
                value = vars(module).get(parts[0], MISSING)
        for part in parts[1:]:
            # Only read namespace dictionaries; do not invoke descriptors or
            # evaluate the annotation's possibly unresolved type argument.
            if not isinstance(value, (ModuleType, type)):
                break
            value = vars(value).get(part, MISSING)
        else:
            if value is not MISSING:
                return value is ClassVar
        return head in (
            "ClassVar",
            "typing.ClassVar",
            "typing_extensions.ClassVar",
        )
    return False


def _field_spec(owner: type[BetterException], name: str) -> _FieldSpec:
    value = owner.__dict__.get(name, MISSING)
    # A slot descriptor stores an instance value; it is no default.
    if isinstance(value, MemberDescriptorType):
        value = MISSING

    if isinstance(value, Field):
        descriptor = cast(Field[object], value)
        if descriptor.init is False or descriptor.kw_only is False:
            raise TypeError(
                f"{owner.__name__}.{name}: only init=True and keyword-only "
                "fields are supported"
            )
        factory = descriptor.default_factory
        if factory is not MISSING and not callable(factory):
            raise TypeError(
                f"{owner.__name__}.{name}: default_factory must be callable"
            )
        secret = descriptor.metadata.get("redact", False)
        if not isinstance(secret, bool):
            raise TypeError(
                f"{owner.__name__}.{name}: metadata['redact'] must be bool"
            )
        spec = _FieldSpec(
            default=descriptor.default,
            factory=factory if factory is not MISSING else None,
            redact=secret,
        )
    else:
        spec = _FieldSpec(default=value)

    if spec.default is not MISSING and type(spec.default).__hash__ is None:
        raise ValueError(
            f"{owner.__name__}.{name}: unhashable default; "
            "use field(default_factory=...)"
        )
    return spec


def _compile_template(
    error_type: type[BetterException],
    field_names: frozenset[str],
    redacted: frozenset[str],
) -> tuple[tuple[str, str | None], ...]:
    template: Any = error_type.message_template
    if not isinstance(template, str):
        raise TypeError(f"{error_type.__name__}.message_template must be str")

    result: list[tuple[str, str | None]] = []
    try:
        for literal, name, format_spec, conversion in Formatter().parse(
            template
        ):
            if name is not None:
                if (
                    not name.isidentifier()
                    or format_spec
                    or conversion is not None
                ):
                    raise ValueError(
                        f"unsupported placeholder {name!r}; "
                        "use simple {field_name}"
                    )
                if name not in field_names:
                    raise ValueError(f"unknown placeholder {name!r}")
                if name in redacted:
                    raise ValueError(
                        f"redacted field {name!r} cannot appear "
                        "in message_template"
                    )
            result.append((literal, name))
    except ValueError as exc:
        raise ValueError(
            f"{error_type.__name__}.message_template: {exc}"
        ) from exc
    return tuple(result)


def _restore_exception(
    error_type: type[BetterException],
    args: tuple[Any, ...],
    state: dict[str, Any] | None = None,
) -> BetterException:
    # Exception's standard pickle reconstructor calls cls(*exc.args), which
    # would violate our keyword-only field contract. Bypass __init__ safely.
    exc = BaseException.__new__(error_type)
    BaseException.__init__(exc, *args)
    # Keep accepting state in the arguments for previously pickled instances.
    if state is not None:
        for name, value in state.items():
            setattr(exc, name, value)
    return exc


@dataclass_transform(
    kw_only_default=True, eq_default=False, field_specifiers=(field,)
)
class _ExceptionRuntime(Exception):
    """Runtime constructor; no declared fields on the transform base."""

    def __init__(self, **kwargs: Any) -> None:
        obj = cast(BetterException, self)
        cls = type(obj)
        if not kwargs.keys() <= cls.__field_names__:
            unknown = kwargs.keys() - cls.__field_names__
            raise TypeError(
                f"{cls.__name__}() got unexpected keyword argument(s): "
                + ", ".join(repr(key) for key in sorted(unknown))
            )
        if cls.__required__ and not cls.__required__ <= kwargs.keys():
            missing = [
                name
                for name in cls.__fields__
                if name in cls.__required__ and name not in kwargs
            ]
            raise TypeError(
                f"{cls.__name__}() missing required keyword-only argument(s): "
                + ", ".join(repr(name) for name in missing)
            )

        store: Callable[[str, Any], None]
        if cls.__setattr__ is BetterException.__setattr__:
            # During construction args is committed once, after rendering.
            # Bypass synchronization while retaining cooperative base setters.
            store = super(BetterException, obj).__setattr__
        else:
            store = obj.__setattr__
        for name, default, factory in cls.__initializers__:
            if factory is None:
                value = kwargs.get(name, default)
            elif name in kwargs:
                value = kwargs[name]
            else:
                value = factory()
            if name == "message":
                # None is an input sentinel until every field is available
                # for rendering; public message assignments require str.
                object.__setattr__(obj, name, value)
            else:
                store(name, value)

        override: Any = obj.message
        if override is not None and not isinstance(override, str):
            raise TypeError(f"{cls.__name__}(): message must be str | None")
        text = override
        if text is None:
            text = cls.__static_message__
            if text is None:
                text = "".join(
                    [
                        literal
                        + (str(getattr(obj, name)) if name is not None else "")
                        for literal, name in cls.__template_parts__
                    ]
                )
        if type(text) is not str:
            text = str.__str__(text)
        store("message", text)
        super().__init__(obj.message)


class BetterException(_ExceptionRuntime):
    """Exception with required/optional typed fields and structured context.

    Define ``message_template`` on subclasses and annotate context fields.
    ``message=...`` overrides the template literally (it is not formatted).
    After construction, assigning a string to ``message`` or a one-string
    tuple to ``args`` updates both attributes. Other assignments, including
    ``message = None``, and deletion of either attribute are rejected.
    ``None`` is only a constructor sentinel for rendering the template.

    Field names and presence are validated, but field value types are checked
    only by a static type checker. Use ``field(default_factory=...)`` for
    mutable defaults. ``field(metadata={"redact": True})`` hides a value
    from ``context`` and forbids its use in the message template. Redaction is
    inherited and cannot be removed by redeclaring a field. It does not filter
    explicit message overrides, exception notes, nested values, or pickle data.

    Redeclaring a field replaces its default or factory; an annotation without
    a default makes it required. Fields and templates are compiled when the
    class is defined; later changes to class declarations are not supported.
    String ClassVar aliases resolve in the defining module or class namespace;
    local function bindings erased by future annotations cannot be recovered.

    >>> class MissingItem(AppException):
    ...     message_template = "Item {item_id} is missing"
    ...     item_id: int
    >>> error = MissingItem(item_id=7)
    >>> str(error)
    'Item 7 is missing'
    >>> error.context
    {'item_id': 7}
    """

    message: str | None = None
    message_template: ClassVar[str] = ""
    __redact__: ClassVar[frozenset[str]] = frozenset()

    __fields__: ClassVar[tuple[str, ...]] = ("message",)
    __field_names__: ClassVar[frozenset[str]] = frozenset({"message"})
    __required__: ClassVar[frozenset[str]] = frozenset()
    __context_fields__: ClassVar[tuple[str, ...]] = ()
    __specs__: ClassVar[Mapping[str, _FieldSpec]] = MappingProxyType(
        {"message": _FieldSpec(default=None)}
    )
    __declared_specs__: ClassVar[Mapping[str, _FieldSpec | None]] = __specs__
    __initializers__: ClassVar[tuple[_FieldInitializer, ...]] = (
        ("message", None, None),
    )
    __template_parts__: ClassVar[tuple[tuple[str, str | None], ...]] = ()
    __static_message__: ClassVar[str | None] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        annotations = _declared_annotations(cls)

        # A legacy-style `message = "..."` would overwrite the instance field
        # while bypassing its definition. Make the mistake explicit.
        if "message" in cls.__dict__ and "message" not in annotations:
            raise TypeError(
                f"{cls.__name__}: use message_template = ... "
                "instead of message = ..."
            )

        specs: dict[str, _FieldSpec] = {}
        redacted: set[str] = set()
        # Merge cached declarations, not whole inherited schemas: the latter
        # would apply the same ancestor twice in a diamond hierarchy.
        for owner in reversed(cls.__mro__[1:]):
            if not issubclass(owner, BetterException):
                continue
            redacted.update(owner.__redact__)
            for name, spec in owner.__declared_specs__.items():
                if spec is None:
                    specs.pop(name, None)
                else:
                    specs[name] = spec

        declared_redacted = cls.__dict__.get("__redact__", frozenset())
        if not isinstance(declared_redacted, (set, frozenset)):
            raise TypeError(
                f"{cls.__name__}.__redact__ must be a set of strings"
            )
        for hidden_name in cast(
            set[object] | frozenset[object], declared_redacted
        ):
            if not isinstance(hidden_name, str):
                raise TypeError(
                    f"{cls.__name__}.__redact__ must be a set of strings"
                )
            redacted.add(hidden_name)

        declared_specs: dict[str, _FieldSpec | None] = {}
        for name, hint in annotations.items():
            if _is_classvar(hint, cls):
                declared_specs[name] = None
                specs.pop(name, None)
                continue
            if (
                not name.isidentifier()
                or name.startswith("_")
                or name in ("args", "context", "with_traceback", "add_note")
            ):
                raise TypeError(
                    f"{cls.__name__}: reserved field name {name!r}"
                )
            spec = _field_spec(cls, name)
            declared_specs[name] = spec
            specs[name] = spec
            if spec.redact:
                redacted.add(name)

        if "message" not in specs:
            raise TypeError(
                f"{cls.__name__}: 'message' must remain an instance field"
            )

        unknown_hidden = redacted.difference(specs)
        if unknown_hidden:
            raise TypeError(
                f"{cls.__name__}: __redact__ contains unknown fields: "
                + ", ".join(sorted(unknown_hidden))
            )
        cls.__fields__ = tuple(specs)
        cls.__field_names__ = frozenset(specs)
        cls.__required__ = frozenset(
            name
            for name, spec in specs.items()
            if spec.default is MISSING and spec.factory is None
        )
        cls.__specs__ = MappingProxyType(specs)
        cls.__declared_specs__ = MappingProxyType(declared_specs)
        cls.__initializers__ = tuple(
            (name, spec.default, spec.factory) for name, spec in specs.items()
        )
        cls.__redact__ = frozenset(redacted)
        cls.__context_fields__ = tuple(
            name
            for name in specs
            if name != "message" and name not in redacted
        )
        cls.__template_parts__ = _compile_template(
            cls, cls.__field_names__, cls.__redact__
        )
        cls.__static_message__ = (
            "".join(literal for literal, _ in cls.__template_parts__)
            if all(name is None for _, name in cls.__template_parts__)
            else None
        )

    @property
    def context(self) -> dict[str, Any]:
        """Return a fresh, shallow dictionary excluding redacted fields.

        Values are unchanged references, without recursive filtering or JSON
        conversion. This mapping is not a general safe-logging serializer.
        """
        return {
            name: getattr(self, name) for name in type(self).__context_fields__
        }

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "args":
            arguments = tuple(value)
            if len(arguments) != 1 or not isinstance(arguments[0], str):
                raise TypeError("args must contain exactly one message string")
            value = arguments[0]
        elif name != "message":
            super().__setattr__(name, value)
            return
        if not isinstance(value, str):
            raise TypeError("message must be str after construction")
        # Normalize str subclasses so BaseException.__str__ cannot invoke a
        # custom __str__ that disagrees with the stored message text.
        text = str.__str__(value)
        super().__setattr__("message", text)
        super().__setattr__("args", (text,))

    def __delattr__(self, name: str) -> None:
        if name == "message":
            raise AttributeError("message may not be deleted")
        super().__delattr__(name)

    def __reduce__(self) -> tuple[Any, tuple[Any, ...], object]:
        # Restore state after memoizing the instance, so deepcopy preserves
        # cycles. The default state keeps __dict__ and all slots separate.
        return (
            _restore_exception,
            (type(self), self.args),
            object.__getstate__(self),
        )

    def __setstate__(self, state: _ExceptionState) -> None:
        """Restore instance storage, including slots outside the schema."""
        # BaseException.__setstate__ only accepts a dictionary, whereas the
        # default object state also includes slots as a separate dictionary.
        if isinstance(state, tuple):
            namespace, slots = state
        else:
            namespace, slots = state, {}
        if namespace is not None:
            self.__dict__.update(namespace)
        for name, value in slots.items():
            setattr(self, name, value)
