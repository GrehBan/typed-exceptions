"""Core exception runtime and class-construction machinery."""

import dataclasses
from collections.abc import Callable, Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    cast,
    dataclass_transform,
)

from typed_exceptions.exception.access import should_redact
from typed_exceptions.exception.field import (
    Field,
    field,
    field_spec,
    fields_proxy,
)
from typed_exceptions.exception.introspection import (
    declared_annotations,
    is_classvar,
)
from typed_exceptions.exception.sentinel import MISSING
from typed_exceptions.exception.template import (
    TEMPLATE_COMPILER,
    DynamicTemplate,
    DynamicTemplatePart,
)

MESSAGE = "message"

type _ExceptionState = (
    dict[str, Any] | tuple[dict[str, Any] | None, dict[str, Any]] | None
)


def _reject_positional_args(cls: type, args: tuple[Any, ...]) -> None:
    """Reject positional constructor arguments with a public class name."""
    if args:
        raise TypeError(f"{cls.__name__}() takes no positional arguments")


def _restore_exception[E: TypedException](
    error_type: type[E],
    args: tuple[Any, ...],
    state: dict[str, Any] | None = None,
) -> E:
    # Exception's standard pickle reconstructor calls cls(*exc.args), which
    # would violate our keyword-only field contract. Bypass __init__ safely.
    exc = BaseException.__new__(error_type)
    BaseException.__init__(exc, *args)
    # Keep accepting state in the arguments for previously pickled instances.
    if state is not None:
        for name, value in state.items():
            setattr(exc, name, value)
    return exc


class FrozenClassAttrsMeta(type):
    """Metaclass that prevents changes to compiled class metadata."""

    __frozen_attrs__ = frozenset(
        {
            MESSAGE,
            "message_template",
            "__redact__",
            "__fields_set__",
            "__field_names__",
            "__required__",
            "__context_fields__",
            "__fields__",
            "__declared_fields__",
            "__initializers__",
            "__static_message__",
            "__dynamic_template__",
        }
    )

    def __setattr__(cls, name: str, value: Any) -> None:
        """Reject changes to frozen attributes declared on the class."""
        if name in type(cls).__frozen_attrs__ and name in vars(cls):
            raise AttributeError(
                f"cannot modify frozen class attribute "
                f"{cls.__qualname__}.{name}"
            )

        super().__setattr__(name, value)

    def __delattr__(cls, name: str) -> None:
        """Reject deletion of frozen attributes declared on the class."""
        if name in type(cls).__frozen_attrs__ and name in vars(cls):
            raise AttributeError(
                f"cannot delete frozen class attribute "
                f"{cls.__qualname__}.{name}"
            )

        super().__delattr__(name)


_RESERVED_FIELD_NAMES = frozenset(
    {
        "args",
        "context",
        "with_traceback",
        "add_note",
    }
)


def _inherited_fields(
    cls: type[TypedException],
) -> tuple[dict[str, Field[Any]], set[str]]:
    fields: dict[str, Field[Any]] = {}
    redacted: set[str] = set()

    for owner in reversed(cls.__mro__[1:]):
        if not issubclass(owner, TypedException):
            continue
        redacted.update(owner.__redact__)
        for name, dfield in owner.__declared_fields__.items():
            if dfield is MISSING:
                fields.pop(name, None)
            else:
                fields[name] = dfield

    return fields, redacted


def _declared_redacted(cls: type[TypedException]) -> frozenset[str]:
    declared = cls.__dict__.get("__redact__", frozenset())
    if not isinstance(declared, (set, frozenset)):
        raise TypeError(f"{cls.__name__}.__redact__ must be a set of strings")

    if not all(isinstance(hidden, str) for hidden in declared):
        raise TypeError(f"{cls.__name__}.__redact__ must be a set of strings")

    return frozenset(declared)


def _is_reserved_field_name(name: str) -> bool:
    return (
        not name.isidentifier()
        or name.startswith("_")
        or name in _RESERVED_FIELD_NAMES
    )


def _collect_fields(
    cls: type[TypedException],
    annotations: Mapping[str, Any],
) -> tuple[
    dict[str, Field[Any]],
    dict[str, Field[Any] | MISSING],
    set[str],
]:
    fields, redacted = _inherited_fields(cls)
    declared_fields: dict[str, Field[Any] | MISSING] = {}

    for name, hint in annotations.items():
        if is_classvar(hint, cls):
            declared_fields[name] = MISSING
            fields.pop(name, None)
            continue

        if _is_reserved_field_name(name):
            raise TypeError(f"{cls.__name__}: reserved field name {name!r}")

        _field: Field[Any] = field_spec(cls, name)
        declared_fields[name] = _field
        fields[name] = _field
        if _field.redact or should_redact(hint, cls):
            redacted.add(name)

    return fields, declared_fields, redacted


def _finalize_fields(
    cls: type[TypedException],
    fields: dict[str, Field[Any]],
    declared_fields: dict[str, Field[Any] | MISSING],
    redacted: set[str],
    declared_redacted: frozenset[str],
) -> None:
    if MESSAGE not in fields:
        raise TypeError(
            f"{cls.__name__}: 'message' must remain an instance field"
        )

    unknown_hidden = {str(hidden) for hidden in declared_redacted}.difference(
        fields
    )
    if unknown_hidden:
        raise TypeError(
            f"{cls.__name__}: __redact__ contains unknown fields: "
            + ", ".join(sorted(unknown_hidden))
        )
    redacted.update(str(hidden) for hidden in declared_redacted)
    # An inherited redacted field may have become a ClassVar here.
    redacted.intersection_update(fields)

    cls.__declared_fields__ = fields_proxy(declared_fields)
    cls.__fields_set__ = frozenset(fields.values())
    cls.__field_names__ = frozenset(fields)
    cls.__required__ = frozenset(
        required
        for required in fields.values()
        if required.name != MESSAGE
        and required.default is MISSING
        and required.factory is MISSING
    )
    cls.__fields__ = fields_proxy(fields)
    cls.__initializers__ = tuple(
        field for field in fields.values() if field.name != MESSAGE
    )
    # A subclass may declare __redact__ in its body; the metaclass
    # guard only protects it from changes after class creation.
    type.__setattr__(cls, "__redact__", frozenset(redacted))
    cls.__context_fields__ = tuple(
        context_field
        for context_field in fields.values()
        if context_field.name != MESSAGE
        and not context_field.reserved
        and context_field.name not in redacted
    )


def _compile_message_template(cls: type[TypedException]) -> None:
    template: Any = cls.message_template
    if not isinstance(template, str):
        raise TypeError(f"{cls.__name__}.message_template must be str")

    # ``message`` is an implicit field but cannot be a template placeholder:
    # it is assigned only after the template is rendered.
    template_fields = cls.__field_names__ - {MESSAGE}
    cls.__dynamic_template__ = DynamicTemplate(
        *(
            DynamicTemplatePart(literal, name)
            for literal, name in TEMPLATE_COMPILER.compile(
                cls,
                template,
                template_fields,
                cls.__redact__,
            )
        )
    )
    cls.__static_message__ = (
        cls.__dynamic_template__.build(cls)
        if cls.__dynamic_template__.is_static
        else MISSING
    )


@dataclass_transform(
    kw_only_default=True,
    eq_default=False,
    field_specifiers=(field, dataclasses.field),
)
class ExceptionRuntime(Exception):
    """Runtime that validates keyword-only fields and renders messages."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the exception from keyword-only field values.

        Args:
            *args: Rejected; construction is keyword-only.
            **kwargs: Field values keyed by field name.

        Raises:
            TypeError: If a positional argument is supplied, a required field
                is missing, an unknown field is supplied, or the message is
                not a string.
        """
        self = cast(TypedException, self)
        cls = type(self)
        _reject_positional_args(cls, args)
        keys = kwargs.keys()
        cls_name = cls.__name__

        if not keys <= cls.__field_names__:
            unknown = sorted(kwargs.keys() - cls.__field_names__)
            raise TypeError(
                f"{cls_name}() got unexpected keyword argument(s): "
                + ", ".join(repr(key) for key in unknown)
            )
        missing = sorted(
            required.name
            for required in cls.__required__
            if required.name not in kwargs
        )
        if missing:
            raise TypeError(
                f"{cls_name}() missing required keyword-only argument(s): "
                + ", ".join(repr(name) for name in missing)
            )

        store: Callable[[str, Any], None]

        if cls.__setattr__ is TypedException.__setattr__:
            store = super(TypedException, self).__setattr__
        else:
            store = self.__setattr__

        for initializer in cls.__initializers__:
            value = initializer.initialize(kwargs)
            if initializer.reserved:
                object.__setattr__(self, initializer.name, value)
            else:
                store(initializer.name, value)

        message_field = cast(Field[str], cls.__fields__[MESSAGE])
        override = message_field.initialize(kwargs)
        if override is not MISSING and not isinstance(override, str):
            raise TypeError(f"{cls_name}(): message must be str | MISSING")
        if override is MISSING:
            override = cls.__static_message__
            if override is MISSING:
                override = cls.__dynamic_template__.build(self)
        if type(override) is not str:
            override = str.__str__(override)
        store(MESSAGE, override)

        super().__init__(self.message)


class TypedException(ExceptionRuntime, metaclass=FrozenClassAttrsMeta):
    """Exception with required/optional typed fields and structured context.

    Define ``message_template`` on subclasses and annotate context fields.
    ``message=...`` overrides the template literally (it is not formatted).
    After construction, assigning a string to ``message`` or a one-string
    tuple to ``args`` updates both attributes. Other assignments, including
    ``message = None``, and deletion of either attribute are rejected.
    Omitting ``message`` (or passing ``MISSING``) renders the template.

    Field names and presence are validated, but field value types are checked
    only by a static type checker. Use ``field(factory=...)`` or
    ``dataclasses.field(default_factory=...)`` for mutable defaults.

    ``field(redact=True)``, ``dataclasses.field(metadata={"redact": True})``,
    a ``Redact[T]`` annotation, or listing the name in ``__redact__`` hides a
    value from ``context`` and forbids its use in the message template.
    ``Redact[T]`` also works in nested and string annotations when the name
    resolves in the class or module namespace. Redaction is inherited and
    cannot be removed by redeclaring a field. It does not filter explicit
    message overrides, exception notes, nested values, or pickle data.

    Redeclaring a field replaces its default or factory; an annotation without
    a default makes it required. Fields and templates are compiled when the
    class is defined; later changes to class declarations are not supported.
    String ClassVar aliases resolve in the defining module or class namespace;
    local function bindings erased by future annotations cannot be recovered.

    >>> class MissingItem(TypedException):
    ...     message_template = "Item {item_id} is missing"
    ...     item_id: int
    >>> error = MissingItem(item_id=7)
    >>> str(error)
    'Item 7 is missing'
    >>> error.context
    {'item_id': 7}
    """

    if TYPE_CHECKING:
        # For type checkers ``message`` is an optional str argument; after
        # construction it is always a str (see __setattr__).
        message: str = ""
        message_template: ClassVar[str]
        __redact__: ClassVar[frozenset[str]]
        __fields_set__: ClassVar[frozenset[Field[Any]]]
        __field_names__: ClassVar[frozenset[str]]
        __required__: ClassVar[frozenset[Field[Any]]]
        __context_fields__: ClassVar[tuple[Field[Any], ...]]
        __fields__: ClassVar[Mapping[str, Field[Any]]]
        __declared_fields__: ClassVar[Mapping[str, Field[Any] | MISSING]]
        __initializers__: ClassVar[tuple[Field[Any], ...]]
        __static_message__: ClassVar[str | MISSING]
        __dynamic_template__: ClassVar[DynamicTemplate]
    else:
        # __set_name__ runs only after the class body, so name it here.
        message = Field(name=MESSAGE, reserved=True)
        message_template = ""
        __redact__ = frozenset()
        __fields_set__ = frozenset({message})
        __field_names__ = frozenset({MESSAGE})
        __required__ = frozenset()
        __context_fields__ = ()
        __fields__ = fields_proxy({MESSAGE: message})
        __declared_fields__ = __fields__
        __initializers__ = ()
        __static_message__ = ""
        __dynamic_template__ = DynamicTemplate()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Compile fields and the message template for a new subclass."""
        super().__init_subclass__(**kwargs)
        annotations = declared_annotations(cls)

        if MESSAGE in cls.__dict__ or MESSAGE in annotations:
            raise TypeError(
                f"{cls.__name__}: 'message' is reserved; "
                "use message_template = ... instead"
            )

        declared_redacted = _declared_redacted(cls)
        fields, declared_fields, redacted = _collect_fields(cls, annotations)
        _finalize_fields(
            cls, fields, declared_fields, redacted, declared_redacted
        )
        _compile_message_template(cls)

    @property
    def context(self) -> dict[str, Any]:
        """Return non-reserved, non-redacted field values in declaration order.

        Returns:
            A new mapping of field names to values.
        """
        return {
            context_field.name: getattr(self, context_field.name)
            for context_field in type(self).__context_fields__
        }

    def __setattr__(self, name: str, value: Any) -> None:
        """Keep ``message`` and ``args`` in sync and reject invalid values."""
        if name == "args":
            arguments = tuple(value)
            if len(arguments) != 1 or not isinstance(arguments[0], str):
                raise TypeError("args must contain exactly one message string")
            value = arguments[0]
        elif name != MESSAGE:
            super().__setattr__(name, value)
            return
        if not isinstance(value, str):
            raise TypeError("message must be str after construction")
        # Normalize str subclasses so BaseException.__str__ cannot invoke a
        # custom __str__ that disagrees with the stored message text.
        text = str.__str__(value)
        super().__setattr__(MESSAGE, text)
        super().__setattr__("args", (text,))

    def __delattr__(self, name: str) -> None:
        """Reject deletion of ``message``."""
        if name == MESSAGE:
            raise AttributeError("message may not be deleted")
        super().__delattr__(name)

    def __reduce__(self) -> tuple[Any, tuple[Any, ...], object]:
        """Return pickle state that bypasses the keyword-only constructor."""
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
            # because of  pyright's reportAttributeAccessIssue
            vars(self).update(namespace)
        for name, value in slots.items():
            setattr(self, name, value)
