"""Field access policy and immutable value-object helpers."""

from functools import lru_cache
from typing import (
    Annotated,
    Any,
    TypeAliasType,
    cast,
    get_args,
    get_origin,
)

from typed_exceptions.exception.introspection import (
    declared_annotations,
    is_classvar,
    resolve_string_annotation,
    string_annotation_head,
)


class _Redact:
    pass


_redact = _Redact()
type Redact[T] = Annotated[T, _redact]

_ALIAS_BY_NAME: dict[str, TypeAliasType] = {
    "Redact": Redact,
    "typed_exceptions.exception.access.Redact": Redact,
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

    Looks through ``Annotated`` wrappers, PEP 695 type aliases, and string
    annotations.

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
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)

        if isinstance(current, str):
            resolved: object | None = resolve_string_annotation(current, owner)
            if resolved is None:
                head = string_annotation_head(current)
                resolved = _ALIAS_BY_NAME.get(head or "")
            if resolved is not None:
                pending.append(resolved)
            continue

        origin = get_origin(current)
        if origin is Annotated:
            arguments = get_args(current)
            if meta_annotation in arguments[1:]:
                return True
            pending.extend(arguments[:1])
            continue

        alias = _type_alias(current, origin)
        if alias is not None:
            pending.append(alias.__value__)

    return False


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


@lru_cache
def collect_attributes(cls: type) -> frozenset[str]:
    """Collect annotated non-``ClassVar`` attributes across the MRO.

    Args:
        cls: Class whose annotations should be inspected.

    Returns:
        Attribute names declared on the class and its bases.
    """
    attrs: set[str] = set()

    for klass in reversed(cls.__mro__):
        if klass is object:
            continue

        for name, annotation in declared_annotations(klass).items():
            if not is_classvar(annotation, klass):
                attrs.add(name)

    return frozenset(attrs)


class FrozenAttributeError(AttributeError):
    """Raised when assigning to or deleting a frozen attribute."""


def frozen[Cls: type](cls: Cls) -> Cls:
    """Make annotated attributes immutable on instances and the class.

    Args:
        cls: Class to freeze.

    Returns:
        The same class with guarded ``__setattr__`` and ``__delattr__``.
    """
    owner: type = cls
    attrs = collect_attributes(owner)

    def is_locked(self: object, name: str) -> bool:
        return type(self) is owner or name in attrs

    def __setattr__(self: Any, name: str, value: Any) -> None:
        if is_locked(self, name):
            raise FrozenAttributeError(
                f"cannot assign to field {name!r} of {type(self).__name__}"
            )

        super(cast(type[Any], owner), self).__setattr__(name, value)

    def __delattr__(self: Any, name: str) -> None:
        if is_locked(self, name):
            raise FrozenAttributeError(
                f"cannot delete field {name!r} of {type(self).__name__}"
            )

        super(cast(type[Any], owner), self).__delattr__(name)

    for method in (__setattr__, __delattr__):
        method.__qualname__ = f"{cls.__qualname__}.{method.__name__}"
        method.__module__ = cls.__module__
        setattr(cls, method.__name__, method)

    return cls
