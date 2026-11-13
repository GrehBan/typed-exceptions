"""Core exception runtime and class-construction machinery."""

import dataclasses
import weakref
from collections.abc import Callable, Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    cast,
    dataclass_transform,
)

from typed_exceptions.access import should_redact
from typed_exceptions.field import (
    Field,
    field,
    field_spec,
    fields_proxy,
)
from typed_exceptions.introspection import (
    declared_annotations,
    is_classvar,
)
from typed_exceptions.sentinel import MISSING
from typed_exceptions.template import (
    TEMPLATE_COMPILER,
    DynamicTemplate,
    DynamicTemplatePart,
)

MESSAGE = "message"
MESSAGE_TEMPLATE = "message_template"
# Instance state of a message that follows its fields: ``True`` for the class
# template, a string for a per-instance template, absent for a literal one.
LIVE_MESSAGE = "__live_message__"

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


def _is_frozen_attr(cls: type, name: str) -> bool:
    """Return whether ``name`` is frozen metadata of ``cls``.

    Compiled attributes are frozen where they are stored. ``message`` and
    ``message_template`` are inherited without being stored on each subclass,
    so they are guarded everywhere: assigning them on a subclass would
    otherwise be a silent no-op, because the template is already compiled.
    """
    if name not in FrozenClassAttrsMeta.__frozen_attrs__:
        return False
    return name in vars(cls) or name in _INHERITED_FROZEN_ATTRS


class FrozenClassAttrsMeta(type):
    """Metaclass that prevents changes to compiled class metadata."""

    __frozen_attrs__ = frozenset(
        {
            MESSAGE,
            MESSAGE_TEMPLATE,
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
        """Reject changes to frozen attributes of the class."""
        if _is_frozen_attr(cls, name):
            raise AttributeError(
                f"cannot modify frozen class attribute "
                f"{cls.__qualname__}.{name}"
            )

        super().__setattr__(name, value)

    def __delattr__(cls, name: str) -> None:
        """Reject deletion of frozen attributes of the class."""
        if _is_frozen_attr(cls, name):
            raise AttributeError(
                f"cannot delete frozen class attribute "
                f"{cls.__qualname__}.{name}"
            )

        super().__delattr__(name)


_INHERITED_FROZEN_ATTRS = frozenset({MESSAGE, MESSAGE_TEMPLATE})

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
        # ``message_template: str = ...`` without ClassVar is still the
        # template, never a context field.
        if name == MESSAGE_TEMPLATE or is_classvar(hint, cls):
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

    if MESSAGE in declared_redacted:
        raise TypeError(
            f"{cls.__name__}: 'message' cannot be listed in __redact__"
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


def _build_template(
    cls: type[TypedException], template: str
) -> DynamicTemplate:
    # ``message`` is an implicit field but cannot be a template placeholder:
    # it is assigned only after the template is rendered.
    template_fields = cls.__field_names__ - {MESSAGE}
    return DynamicTemplate(
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


_INSTANCE_TEMPLATE_LIMIT = 256
_INSTANCE_TEMPLATES: weakref.WeakKeyDictionary[
    type[TypedException], dict[str, DynamicTemplate]
] = weakref.WeakKeyDictionary()


def _instance_template(
    cls: type[TypedException], template: str
) -> DynamicTemplate:
    """Compile a per-instance template once per class and source string.

    The cache is keyed weakly by class, so dynamically created classes are
    still collected, and each class keeps a bounded number of templates.
    """
    cache = _INSTANCE_TEMPLATES.setdefault(cls, {})
    compiled = cache.get(template)
    if compiled is None:
        compiled = _build_template(cls, template)
        if len(cache) >= _INSTANCE_TEMPLATE_LIMIT:
            cache.pop(next(iter(cache)))
        cache[template] = compiled
    return compiled


def _render_message(
    exc: TypedException, override: object, template_override: object
) -> tuple[str, bool | str | None]:
    """Render the message for ``exc`` from the constructor arguments.

    Returns:
        The message and what it must follow when fields change: ``True``
        for the class template, the template string for a per-instance
        template, or ``None`` for a literal or placeholder-free message.
    """
    cls = type(exc)
    cls_name = cls.__name__
    if override is not MISSING and not isinstance(override, str):
        raise TypeError(f"{cls_name}(): message must be str | MISSING")
    if template_override is not MISSING:
        if override is not MISSING:
            raise TypeError(
                f"{cls_name}(): pass either message or message_template, "
                "not both"
            )
        if not isinstance(template_override, str):
            raise TypeError(
                f"{cls_name}(): message_template must be str | MISSING"
            )
        template = _instance_template(cls, template_override)
        source = template_override if template.names else None
        return template.build(exc), source
    if override is MISSING:
        static = cls.__static_message__
        if static is MISSING:
            return cls.__dynamic_template__.build(exc), True
        return static, None
    return str.__str__(override), None


def _refresh_message(exc: TypedException, name: str) -> None:
    """Re-render a live message after the field ``name`` was assigned."""
    source = exc.__live_message__
    if source is None:
        return
    cls = type(exc)
    if source is True:
        template = cls.__dynamic_template__
    else:
        template = _instance_template(cls, cast(str, source))
    if name in template.names:
        try:
            text = template.build(exc)
        except AttributeError:
            # A placeholder field was deleted; keep the last message
            # instead of failing an unrelated assignment.
            return
        _store_message(exc, text)


def _store_message(exc: TypedException, text: str) -> None:
    """Store ``text`` as ``message`` and ``args`` without side effects."""
    # Going past TypedException.__setattr__ avoids its literal-message switch
    # while still honouring any other base class in the MRO.
    parent = super(TypedException, exc)
    parent.__setattr__(MESSAGE, text)
    parent.__setattr__("args", (text,))


def _compile_message_template(cls: type[TypedException]) -> None:
    template: Any = cls.message_template
    if not isinstance(template, str):
        raise TypeError(f"{cls.__name__}.message_template must be str")

    cls.__dynamic_template__ = _build_template(cls, template)
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

    def __init__(self, /, *args: Any, **kwargs: Any) -> None:
        """Initialize the exception from keyword-only field values.

        Args:
            *args: Rejected; construction is keyword-only.
            **kwargs: Field values keyed by field name, plus the optional
                ``message`` (literal text) and ``message_template`` (a
                template used instead of the class one) overrides.

        Raises:
            TypeError: If a positional argument is supplied, a required field
                is missing, an unknown field is supplied, the message or
                ``message_template`` is not a string, or both are given.
            ValueError: If ``message_template`` has an unsupported, unknown,
                or redacted placeholder.
        """
        self = cast(TypedException, self)
        cls = type(self)
        _reject_positional_args(cls, args)
        template_override = kwargs.pop(MESSAGE_TEMPLATE, MISSING)
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
            if kwargs.get(required.name, MISSING) is MISSING
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
        text, live = _render_message(self, override, template_override)
        store(MESSAGE, text)
        if live is not None:
            object.__setattr__(self, LIVE_MESSAGE, live)
        else:
            # Calling __init__ again must not keep following the old template.
            vars(self).pop(LIVE_MESSAGE, None)

        super().__init__(self.message)


class TypedException(ExceptionRuntime, metaclass=FrozenClassAttrsMeta):
    """Exception with required/optional typed fields and structured context.

    Define ``message_template`` on subclasses and annotate context fields.
    ``message=...`` overrides the template literally (it is not formatted).
    ``message_template=...`` renders a different template for one instance;
    it is validated like the class template and cannot be combined with
    ``message=``.
    After construction, assigning a string to ``message`` or a one-string
    tuple to ``args`` updates both attributes. Other assignments, including
    ``message = None``, and deletion of either attribute are rejected.
    Omitting ``message`` (or passing ``MISSING``) renders the template.
    A rendered message follows its fields: assigning a field used by the
    template re-renders ``message`` and ``args``. A literal ``message=...``,
    or an explicit assignment to ``message`` or ``args``, is never replaced,
    and in-place changes to a value are not detected.

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
        # For type checkers ``message_template`` is also an optional str
        # constructor argument (a per-instance template).
        message_template: str = ""
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
        __live_message__: ClassVar[bool | str | None]
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
        __live_message__ = None

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
            _refresh_message(self, name)
            return
        if not isinstance(value, str):
            raise TypeError("message must be str after construction")
        # Normalize str subclasses so BaseException.__str__ cannot invoke a
        # custom __str__ that disagrees with the stored message text.
        _store_message(self, str.__str__(value))
        # An explicit message is literal from now on: stop following fields.
        vars(self).pop(LIVE_MESSAGE, None)

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
