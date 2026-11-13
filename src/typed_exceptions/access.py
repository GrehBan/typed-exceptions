"""Field access policy: the ``Redact`` marker and its detection."""

from typing import (
    Annotated,
    ForwardRef,
    Literal,
    TypeAliasType,
    get_args,
    get_origin,
)

from typed_exceptions.introspection import annotation_names
from typed_exceptions.sentinel import MISSING


class _Redact:
    pass


_redact = _Redact()
type Redact[T] = Annotated[T, _redact]

_ALIAS_BY_NAME: dict[str, TypeAliasType] = {
    "Redact": Redact,
    "typed_exceptions.access.Redact": Redact,
}


def should_redact(annotation: object, owner: type) -> bool:
    """Return whether ``annotation`` is marked for redaction.

    Args:
        annotation: Annotation to inspect.
        owner: Class that declares the annotation.

    Returns:
        ``True`` if the annotation carries the redaction marker.
    """
    return meta_annotation_exists(annotation, _redact, owner)


def meta_annotation_exists(
    annotation: object, meta_annotation: object, owner: type
) -> bool:
    """Return whether ``annotation`` carries ``meta_annotation``.

    The marker is looked for anywhere in the annotation: inside
    ``Annotated`` wrappers, unions and ``Optional``, generic arguments,
    PEP 695 type aliases, forward references, and string annotations.

    Args:
        annotation: Annotation to inspect.
        meta_annotation: Metadata object to look for.
        owner: Class that declares the annotation.

    Returns:
        ``True`` if the metadata is present, otherwise ``False``.
    """
    pending: list[object] = [annotation]
    seen: set[int] = set()

    while pending:
        current = pending.pop()
        if isinstance(current, (list, tuple)):
            # Parameter lists such as the one in ``Callable[[X], Y]``.
            pending.extend(current)
            continue

        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)

        if isinstance(current, ForwardRef):
            pending.extend(_forward_ref_parts(current))
        elif isinstance(current, str):
            pending.extend(_referenced_objects(current, owner))
        elif _expand(current, meta_annotation, pending):
            return True

    return False


def _expand(
    annotation: object, meta_annotation: object, pending: list[object]
) -> bool:
    """Queue what ``annotation`` is made of; report a direct marker hit."""
    origin = get_origin(annotation)
    if origin is Annotated:
        arguments = get_args(annotation)
        if any(item is meta_annotation for item in arguments[1:]):
            return True
        pending.append(arguments[0])
        return False

    alias = _type_alias(annotation, origin)
    if alias is not None:
        pending.append(alias.__value__)
    if origin is not Literal:
        pending.extend(get_args(annotation))
    return False


def _forward_ref_parts(reference: ForwardRef) -> list[object]:
    """Return what a forward reference is made of.

    PEP 649 hands out a reference when a class body mentions a name that is
    not defined yet. Its text then stands in for the parts that did resolve
    with placeholder names (``__annotationlib_name_1__ | Later``) and keeps
    the resolved objects in ``__extra_names__``. That attribute is not part
    of the documented API, so it is read defensively: without it only the
    text is inspected.
    """
    parts: list[object] = [reference.__forward_arg__]
    resolved = getattr(reference, "__extra_names__", None)
    if isinstance(resolved, dict):
        parts.extend(resolved.values())
    return parts


def _referenced_objects(annotation: str, owner: type) -> list[object]:
    """Resolve the names in a string annotation without evaluating it."""
    found: list[object] = []
    for name, value in annotation_names(annotation, owner):
        if value is MISSING:
            # The alias may be imported where the namespaces cannot see it.
            value = _ALIAS_BY_NAME.get(name, MISSING)
        if value is not MISSING:
            found.append(value)
    return found


def _type_alias(annotation: object, origin: object) -> TypeAliasType | None:
    """Return the PEP 695 alias behind an annotation, if any.

    Args:
        annotation: Annotation to inspect.
        origin: Result of ``get_origin(annotation)``.

    Returns:
        The alias object, or ``None`` when the annotation is not an alias.
    """
    if isinstance(annotation, TypeAliasType):
        return annotation
    if isinstance(origin, TypeAliasType):
        return origin
    return None
