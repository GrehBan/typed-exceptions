"""Field specifications and dataclass interoperability."""

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType, MemberDescriptorType
from typing import (
    Any,
    Final,
    Protocol,
    TypeIs,
    cast,
    overload,
    runtime_checkable,
)

from typed_exceptions.sentinel import MISSING

DEFAULT_REDACT: Final[bool] = False
DEFAULT_RESERVED: Final[bool] = False
type DefaultAttr[T] = T | MISSING
type FactoryAttr[T] = Factory[T] | MISSING


@runtime_checkable
class Factory[T](Protocol):
    """Callable that produces a fresh default value."""

    def __call__(self) -> T:
        """Return a new default value."""
        ...


def field_spec(owner: type, name: str) -> Field[Any]:
    """Build a fresh field specification from a class attribute.

    Args:
        owner: Class that declares the field.
        name: Field name.

    Returns:
        A new immutable field specification bound to ``name``.

    Raises:
        ValueError: If the default value is unhashable.
    """
    value = vars(owner).get(name, MISSING)
    if isinstance(value, MemberDescriptorType):
        value = MISSING

    if isinstance(value, dataclasses.Field):
        spec = transform_from_dataclass_field(value)
    elif isinstance(value, Field):
        spec = value
    else:
        spec = Field(default=value)

    # Always bind a fresh copy: the declared object may be shared between
    # classes or names, and Field instances are immutable.
    spec = Field(
        default=spec.default,
        factory=spec.factory,
        redact=spec.redact,
        reserved=spec.reserved,
        name=name,
    )

    if spec.default is not MISSING and not _is_hashable(spec.default):
        raise ValueError(
            f"{owner.__name__}.{name}: unhashable default; "
            "use field(default_factory=...)"
        )
    return spec


def is_dataclass_missing(value: object) -> TypeIs[dataclasses._MISSING_TYPE]:
    """Return whether ``value`` is a dataclass ``MISSING`` sentinel."""
    return isinstance(value, dataclasses._MISSING_TYPE)


def _is_hashable(value: object) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


@dataclasses.dataclass(frozen=True, slots=True, repr=False)
class Field[T]:
    """Immutable specification of one exception field.

    The constructor takes ``default``, ``factory``, ``redact``, ``reserved``
    and ``name`` in that order. ``name`` is usually bound later by
    ``__set_name__``.

    Attributes:
        default: Default value, or ``MISSING``.
        factory: Zero-argument default factory, or ``MISSING``.
        redact: Whether the value is hidden from ``context`` and templates.
        reserved: Whether the value is hidden from ``context``.
        name: Field name.
    """

    # Declaration order is the positional constructor order.
    default: DefaultAttr[T] = MISSING
    factory: FactoryAttr[T] = MISSING
    redact: bool = DEFAULT_REDACT
    reserved: bool = DEFAULT_RESERVED
    name: str = ""

    def __set_name__(self, owner: type, name: str) -> None:
        """Bind the attribute name when the field is declared in a class."""
        object.__setattr__(self, "name", name)

    def initialize(self, namespace: dict[str, Any]) -> DefaultAttr[T]:
        """Resolve the field value from constructor arguments.

        Args:
            namespace: Keyword arguments passed to the exception constructor.

        Returns:
            The supplied value, the factory result, the default, or
            ``MISSING`` when the field is required.
        """
        supplied: object = namespace.get(self.name, MISSING)
        if supplied is not MISSING:
            return cast(T, supplied)
        factory = self.factory
        if factory is MISSING:
            return self.default
        return factory()

    def __repr__(self) -> str:
        """Return a debug representation of the field.

        Kept by hand so ``name`` comes first, unlike the constructor order.
        """
        return (
            f"Field(name={self.name!r}, default={self.default!r}, "
            f"factory={self.factory!r}, redact={self.redact!r}, "
            f"reserved={self.reserved!r})"
        )


def fields_proxy[F: Field[Any] | MISSING](
    fields: Mapping[str, F],
) -> Mapping[str, F]:
    """Return a read-only snapshot of ``fields``, keeping its order."""
    return MappingProxyType(dict(fields))


# Like dataclasses.field, the overloads describe the value a declaration
# stands for, so ``x: int = field(default=0)`` type-checks. At runtime the
# call returns a Field specification that __init_subclass__ collects.
@overload
def field[T](
    *,
    default: T,
    redact: bool = DEFAULT_REDACT,
    reserved: bool = DEFAULT_RESERVED,
) -> T: ...


@overload
def field[T](
    *,
    factory: Factory[T],
    redact: bool = DEFAULT_REDACT,
    reserved: bool = DEFAULT_RESERVED,
) -> T: ...


@overload
def field(
    *,
    redact: bool = DEFAULT_REDACT,
    reserved: bool = DEFAULT_RESERVED,
) -> Any: ...


def field(
    *,
    default: Any = MISSING,
    factory: Factory[Any] | MISSING = MISSING,
    redact: bool = DEFAULT_REDACT,
    reserved: bool = DEFAULT_RESERVED,
) -> Any:
    """Declare a field on a ``TypedException`` subclass.

    Args:
        default: Default value, or ``MISSING``.
        factory: Zero-argument callable that produces a default value.
        redact: Hide the value from ``context`` and forbid it in templates.
        reserved: Hide the value from ``context``.

    Returns:
        A field specification for ``__init_subclass__`` to collect.

    Raises:
        ValueError: If both ``default`` and ``factory`` are provided.
    """
    if default is not MISSING and factory is not MISSING:
        raise ValueError("cannot specify both default and factory")
    return Field(
        default=default, factory=factory, redact=redact, reserved=reserved
    )


def transform_dataclass_field_attrs[T](
    field: dataclasses.Field[T],
) -> tuple[DefaultAttr[T], FactoryAttr[T], bool, bool]:
    """Validate a dataclass field and extract its configuration.

    Args:
        field: Dataclass field to convert.

    Returns:
        A tuple of default, factory, redact flag, and reserved flag.

    Raises:
        TypeError: If the field is not keyword-only, uses ``init=False``,
            has a non-callable factory, or has invalid metadata.
    """
    if field.init is False or field.kw_only is False:
        raise TypeError("only init=True and keyword-only fields are supported")

    if field.default_factory is not dataclasses.MISSING and not callable(
        field.default_factory
    ):
        raise TypeError(
            "default_factory must be callable and returns final object"
        )

    redact = field.metadata.get("redact", DEFAULT_REDACT)
    if not isinstance(redact, bool):
        raise TypeError("metadata['redact'] must be bool")

    reserved = field.metadata.get("reserved", DEFAULT_RESERVED)
    if not isinstance(reserved, bool):
        raise TypeError("metadata['reserved'] must be bool")

    default: DefaultAttr[T] = (
        MISSING if is_dataclass_missing(field.default) else field.default
    )
    factory: FactoryAttr[T] = (
        MISSING
        if is_dataclass_missing(field.default_factory)
        else field.default_factory
    )
    return default, factory, redact, reserved


def transform_from_dataclass_field[T](
    field: dataclasses.Field[T],
) -> Field[T]:
    """Convert a dataclass field into an exception field specification.

    Args:
        field: Dataclass field to convert.

    Returns:
        A new field specification.
    """
    return Field(*transform_dataclass_field_attrs(field))
