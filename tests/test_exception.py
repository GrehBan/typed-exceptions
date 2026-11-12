"""Tests for TypedException fields, templates, and runtime behavior."""

import copy
import dataclasses
import doctest
import pickle
import typing
from typing import Annotated, Any, ClassVar, cast

import pytest

from typed_exceptions.exception import (
    MISSING,
    Field,
    Redact,
    TypedException,
    field,
)
from typed_exceptions.exception import exception as exception_module
from typed_exceptions.exception.access import frozen
from typed_exceptions.exception.introspection import string_annotation_head


class UserNotFound(TypedException):
    """Exception with one required integer field."""

    message_template = "User {user_id} was not found"
    user_id: int


class InvalidToken(TypedException):
    """Exception with two required string fields."""

    message_template = "Token {token_id} is invalid for {scope}"
    token_id: str
    scope: str


class ConfigWarning(TypedException):
    """Exception with an optional string and a mutable list."""

    message_template = "Config {name} is using a deprecated option"
    name: str
    detail: str = "unknown"
    tags: list[str] = dataclasses.field(default_factory=list)


class SecretAccess(TypedException):
    """Exception whose API key is redacted via dataclass metadata."""

    message_template = "Access denied for {user}"
    user: str
    api_key: str = dataclasses.field(default="", metadata={"redact": True})


class LayerFailure(TypedException):
    """Exception used to test literal message overrides."""

    message_template = "Failure in {layer}"
    layer: str


class Base(TypedException):
    """Base exception with an inherited redacted field."""

    message_template = "base {a}"
    a: int
    secret: str = field(default="", redact=True)


class Child(Base):
    """Child exception that adds one optional field."""

    b: str = "b"


class GrandChild(Child):
    """Grandchild exception with its own template and field."""

    message_template = "grand {a} {b} {c}"
    c: float = 1.5


class SlottedFailure(TypedException):
    """Exception with a field stored in a slot."""

    __slots__ = ("trace_id",)
    message_template = "Failure {trace_id}"
    trace_id: str


# --- README / docstring examples ---


def test_quick_start() -> None:
    """Verify the quick-start exception renders and exposes context."""
    error = UserNotFound(user_id=42)
    assert str(error) == "User 42 was not found"
    assert error.message == "User 42 was not found"
    assert error.args == ("User 42 was not found",)
    assert error.context == {"user_id": 42}
    assert error.user_id == 42


def test_docstring_example() -> None:
    """Run the doctest example from the TypedException docstring."""
    globs = {"TypedException": TypedException}
    finder = doctest.DocTestFinder()
    runner = doctest.DocTestRunner()
    for test in finder.find(TypedException, globs=globs):
        runner.run(test)
    assert runner.failures == 0
    assert runner.tries > 0


def test_multiple_placeholders() -> None:
    """Verify multiple template placeholders are rendered."""
    error = InvalidToken(token_id="abc", scope="admin")
    assert str(error) == "Token abc is invalid for admin"


def test_is_a_regular_exception() -> None:
    """Verify TypedException behaves like a regular exception."""
    with pytest.raises(UserNotFound) as info:
        raise UserNotFound(user_id=1)
    assert isinstance(info.value, Exception)


# --- constructor contract ---


def test_missing_required_argument() -> None:
    """Verify a missing required field raises TypeError."""
    with pytest.raises(TypeError, match=r"missing .* 'token_id'$"):
        InvalidToken(scope="admin")  # type: ignore[call-arg]


def test_missing_required_arguments_are_sorted() -> None:
    """Verify missing field names are reported in sorted order."""
    with pytest.raises(TypeError, match=r"'scope', 'token_id'$"):
        InvalidToken()  # type: ignore[call-arg]


def test_unexpected_argument() -> None:
    """Verify an unknown keyword argument raises TypeError."""
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        UserNotFound(user_id=1, nope=2)  # type: ignore[call-arg]


def test_positional_arguments_rejected() -> None:
    """Verify positional constructor arguments are rejected."""
    with pytest.raises(TypeError):
        UserNotFound(1)  # type: ignore


def test_positional_argument_error_names_public_class() -> None:
    """Verify the positional error does not leak the internal runtime name."""
    with pytest.raises(
        TypeError, match=r"UserNotFound\(\) takes no positional arguments"
    ):
        UserNotFound(1)  # type: ignore


def test_defaults_and_factories() -> None:
    """Verify defaults and factories produce independent values."""
    first = ConfigWarning(name="a")
    second = ConfigWarning(name="b")
    assert first.detail == "unknown"
    assert first.tags == []
    assert first.tags is not second.tags


def test_own_field_factory() -> None:
    """Verify the custom field factory creates fresh mutable values."""

    class WithFactory(TypedException):
        items: list[int] = field(factory=list)

    assert WithFactory().items == []
    assert WithFactory().items is not WithFactory().items
    assert WithFactory(items=[1]).items == [1]


def test_field_spec_repr_and_immutability() -> None:
    """Verify field specifications are readable and immutable."""
    spec = Field[int](default=1)
    assert "Field(name=''" in repr(spec)
    with pytest.raises(AttributeError):
        spec.default = 2
    with pytest.raises(AttributeError):
        del spec.default


def test_field_spec_subclass_can_store_extra_attributes() -> None:
    """Verify frozen field behavior allows subclass-only attributes."""

    class ExtensibleField(Field[int]):
        pass

    spec = ExtensibleField(default=1)
    setattr(spec, "extra", "value")
    assert getattr(spec, "extra") == "value"
    delattr(spec, "extra")
    assert not hasattr(spec, "extra")


def test_invalid_dataclass_field_options() -> None:
    """Verify unsupported dataclass fields fail during class creation."""
    non_callable_factory: dataclasses.Field[Any] = dataclasses.field()
    object.__setattr__(non_callable_factory, "default_factory", 1)
    invalid_fields: list[tuple[object, str]] = [
        (dataclasses.field(init=False), "only init=True"),
        (dataclasses.field(kw_only=False), "only init=True"),
        (non_callable_factory, "default_factory must be callable"),
        (dataclasses.field(default="", metadata={"redact": 1}), "redact"),
        (dataclasses.field(default="", metadata={"reserved": 1}), "reserved"),
    ]

    for data_field, error in invalid_fields:
        with pytest.raises(TypeError, match=error):
            type(
                "Bad",
                (TypedException,),
                {"__annotations__": {"value": str}, "value": data_field},
            )


def test_field_default_and_factory_are_exclusive() -> None:
    """Verify default and factory cannot be combined."""
    with pytest.raises(ValueError, match="both default and factory"):
        field(default=1, factory=int)  # type: ignore[call-overload]


def test_field_arguments_are_keyword_only() -> None:
    """Verify custom field arguments are keyword-only."""
    with pytest.raises(TypeError):
        field(1)  # type: ignore[call-overload]


def test_mutable_default_rejected() -> None:
    """Verify mutable class-level defaults are rejected."""
    with pytest.raises(ValueError, match="unhashable default"):

        class Bad(TypedException):
            items: list[int] = []  # noqa: RUF012


def test_class_var_is_not_a_field() -> None:
    """Verify ClassVar annotations are excluded from fields."""

    class WithClassVar(TypedException):
        code: ClassVar[int] = 404
        detail: str = "x"

    assert "code" not in WithClassVar.__field_names__
    assert WithClassVar().context == {"detail": "x"}


def test_stringized_dotted_class_var_resolves_from_module() -> None:
    """Verify a stringized dotted ClassVar resolves in the module namespace."""
    namespace: dict[str, Any] = {
        "__name__": __name__,
        "TypedException": TypedException,
        "typing": typing,
    }
    exec(
        "from __future__ import annotations\n"
        "class WithDottedClassVar(TypedException):\n"
        "    code: typing.ClassVar[int] = 404\n"
        "    detail: int = 1\n",
        namespace,
    )
    cls = cast(type[TypedException], namespace["WithDottedClassVar"])
    assert cls().context == {"detail": 1}


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        ("typing.ClassVar[int]", "typing.ClassVar"),
        ("'typing.ClassVar[int]'", "typing.ClassVar"),
        ('"unterminated', None),
        ("bad-name", None),
    ],
)
def test_string_annotation_head(annotation: str, expected: str | None) -> None:
    """Verify string annotation heads are normalized and validated."""
    assert string_annotation_head(annotation) == expected


@pytest.mark.parametrize("name", ["args", "context", "_private"])
def test_reserved_field_names(name: str) -> None:
    """Verify reserved attribute names cannot be fields."""
    with pytest.raises(TypeError, match="reserved field name"):
        type(
            "Bad",
            (TypedException,),
            {"__annotations__": {name: int}},
        )


def test_message_assignment_in_body_rejected() -> None:
    """Verify assigning message in a class body is rejected."""
    with pytest.raises(TypeError, match="message_template"):

        class Bad(TypedException):
            message = "nope"


def test_annotated_message_in_body_rejected() -> None:
    """Verify an annotated message declaration is rejected."""
    with pytest.raises(TypeError, match="message_template"):

        class Bad(TypedException):
            message: str = "nope"


def test_message_template_must_be_str() -> None:
    """Verify message_template must be a string."""
    with pytest.raises(TypeError, match="must be str"):

        class Bad(TypedException):
            message_template = 1  # type: ignore[assignment]


# --- message ---


def test_message_override_is_literal() -> None:
    """Verify a message override is not formatted."""
    error = LayerFailure(layer="cache", message="Cache {layer} unavailable")
    assert str(error) == "Cache {layer} unavailable"


def test_message_missing_renders_template() -> None:
    """Verify MISSING renders the message template."""
    error = LayerFailure(layer="db", message=MISSING)  # type: ignore[arg-type]
    assert str(error) == "Failure in db"


def test_message_must_be_str() -> None:
    """Verify a non-string message override is rejected."""
    with pytest.raises(TypeError, match="message must be str"):
        LayerFailure(layer="db", message=1)  # type: ignore[arg-type]


def test_message_str_subclass_is_normalized() -> None:
    """Verify str subclasses are normalized to plain str."""

    class Loud(str):
        def __str__(self) -> str:
            return "LOUD"

    error = LayerFailure(layer="db", message=Loud("quiet"))
    assert type(error.message) is str
    assert str(error) == "quiet"


def test_static_template() -> None:
    """Verify a template without placeholders is cached statically."""

    class Static(TypedException):
        message_template = "always the same"
        value: int = 0

    assert Static.__static_message__ == "always the same"
    assert str(Static(value=3)) == "always the same"


def test_template_uses_instance_values_not_class_defaults() -> None:
    """Verify templates read instance values, not class defaults."""

    class Defaults(TypedException):
        message_template = "value={value}"
        value: str = dataclasses.field(default="default")

    assert str(Defaults()) == "value=default"
    assert str(Defaults(value="real")) == "value=real"


def test_template_uses_str_not_repr() -> None:
    """Verify template placeholders use str, not repr."""
    assert str(InvalidToken(token_id="a", scope="b")) == (
        "Token a is invalid for b"
    )


def test_escaped_braces() -> None:
    """Verify doubled braces render as literal braces."""

    class Braces(TypedException):
        message_template = "{{literal}} {x}"
        x: int

    assert str(Braces(x=1)) == "{literal} 1"


def test_message_field_is_not_a_template_placeholder() -> None:
    """Verify the implicit message field cannot be a placeholder."""
    with pytest.raises(ValueError, match="unknown placeholder 'message'"):

        class Bad(TypedException):
            message_template = "{message}"
            x: int = 0


def test_base_exception_without_fields() -> None:
    """Verify the base exception works without custom fields."""
    assert str(TypedException()) == ""
    assert str(TypedException(message="plain")) == "plain"
    assert TypedException().context == {}


@pytest.mark.parametrize(
    ("template", "error"),
    [
        ("{missing}", "unknown placeholder"),
        ("{x!r}", "unsupported placeholder"),
        ("{x:>5}", "unsupported placeholder"),
        ("{}", "unsupported placeholder"),
        ("{x.real}", "unsupported placeholder"),
    ],
)
def test_invalid_templates(template: str, error: str) -> None:
    """Verify invalid template placeholders raise ValueError."""
    with pytest.raises(ValueError, match=error):
        type(
            "Bad",
            (TypedException,),
            {"message_template": template, "__annotations__": {"x": int}},
        )


# --- message / args after construction ---


def test_assign_message_updates_args() -> None:
    """Verify assigning message updates args."""
    error = UserNotFound(user_id=1)
    error.message = "changed"
    assert error.args == ("changed",)
    assert str(error) == "changed"


def test_assign_args_updates_message() -> None:
    """Verify assigning args updates message."""
    error = UserNotFound(user_id=1)
    error.args = ("changed",)
    assert error.message == "changed"


@pytest.mark.parametrize("value", [None, 1])
def test_assign_non_str_message_rejected(value: object) -> None:
    """Verify assigning a non-string message is rejected."""
    error = UserNotFound(user_id=1)
    with pytest.raises(TypeError):
        error.message = value  # type: ignore[assignment]


@pytest.mark.parametrize("value", [(), ("a", "b"), (1,)])
def test_assign_invalid_args_rejected(value: tuple[object, ...]) -> None:
    """Verify assigning invalid args is rejected."""
    error = UserNotFound(user_id=1)
    with pytest.raises(TypeError):
        error.args = value


def test_delete_message_rejected() -> None:
    """Verify deleting message is rejected."""
    error = UserNotFound(user_id=1)
    with pytest.raises(AttributeError):
        del error.message


def test_delete_extra_attribute() -> None:
    """Verify deleting a non-message instance attribute is allowed."""
    error = UserNotFound(user_id=1)
    error.extra = "temporary"
    delattr(error, "extra")
    assert not hasattr(error, "extra")


def test_custom_setattr_is_used_during_initialization() -> None:
    """Verify a subclass setter is honored for declared fields."""

    class Normalized(TypedException):
        message_template = "value={value}"
        value: int

        def __setattr__(self, name: str, value: Any) -> None:
            if name == "value":
                value += 1
            super().__setattr__(name, value)

    error = Normalized(value=1)
    assert error.value == 2
    assert str(error) == "value=2"


def test_fields_are_mutable() -> None:
    """Verify field values remain mutable after construction."""
    error = UserNotFound(user_id=1)
    error.user_id = 2
    assert error.context == {"user_id": 2}


# --- redaction ---


def test_redacted_via_dataclass_metadata() -> None:
    """Verify dataclass metadata can redact a field."""
    error = SecretAccess(user="alice", api_key="super-secret")
    assert error.context == {"user": "alice"}
    assert error.api_key == "super-secret"


def test_redacted_via_own_field() -> None:
    """Verify the custom field helper can redact a field."""
    error = Base(a=1, secret="s3cr3t")
    assert error.context == {"a": 1}
    assert "secret" in Base.__redact__


def test_redacted_via_dunder_redact() -> None:
    """Verify __redact__ hides fields from context."""

    class Declared(TypedException):
        __redact__ = frozenset({"token"})
        token: str = ""
        user: str = ""

    error = Declared(token="t", user="u")
    assert error.context == {"user": "u"}
    assert Declared.__redact__ == frozenset({"token"})


def test_redaction_is_inherited_and_not_removable() -> None:
    """Verify redaction is inherited and cannot be removed."""

    class Redeclared(Base):
        secret: str = "plain now?"

    assert "secret" in Redeclared.__redact__
    assert Redeclared(a=1).context == {"a": 1}


def test_redacted_placeholder_rejected() -> None:
    """Verify redacted fields cannot appear in templates."""
    with pytest.raises(ValueError, match="redacted field 'secret'"):

        class Leaky(Base):
            message_template = "{secret}"


def test_unknown_redact_name_rejected() -> None:
    """Verify __redact__ rejects unknown field names."""
    with pytest.raises(TypeError, match="unknown fields: nope"):

        class Bad(TypedException):
            __redact__ = frozenset({"nope"})


def test_redact_must_be_set_of_str() -> None:
    """Verify __redact__ must be a set of strings."""
    with pytest.raises(TypeError, match="set of strings"):

        class BadType(TypedException):
            __redact__ = ["x"]  # type: ignore[assignment]

    with pytest.raises(TypeError, match="set of strings"):

        class BadItem(TypedException):
            __redact__ = frozenset({1})  # type: ignore[arg-type]


def test_reserved_field_hidden_from_context() -> None:
    """Verify reserved fields are hidden from context."""

    class WithReserved(TypedException):
        internal: int = field(default=0, reserved=True)
        public: int = 0

    error = WithReserved(internal=5)
    assert error.internal == 5
    assert error.context == {"public": 0}


# --- annotation markers ---


def test_redact_annotation() -> None:
    """Verify Redact[T] hides a field from context."""

    class WithRedact(TypedException):
        message_template = "user={user}"
        user: str
        token: Redact[str] = ""

    error = WithRedact(user="alice", token="secret")
    assert error.context == {"user": "alice"}
    assert error.token == "secret"
    assert WithRedact.__redact__ == frozenset({"token"})


def test_redact_annotation_rejects_template_placeholder() -> None:
    """Verify Redact[T] fields cannot appear in templates."""
    with pytest.raises(ValueError, match="redacted field 'token'"):

        class Leaky(TypedException):
            message_template = "{token}"
            token: Redact[str] = ""


def test_redact_annotation_nested_in_annotated() -> None:
    """Verify Redact[T] is detected inside Annotated."""

    class Nested(TypedException):
        token: Annotated[Redact[str], "extra"] = ""

    assert Nested(token="secret").context == {}
    assert Nested.__redact__ == frozenset({"token"})


def test_redact_annotation_through_another_alias() -> None:
    """Verify Redact[T] is detected through a nested PEP 695 alias."""
    type Secret[T] = Redact[T]

    class Nested(TypedException):
        token: Secret[str] = ""

    assert Nested(token="secret").context == {}
    assert Nested.__redact__ == frozenset({"token"})


def test_redact_annotation_in_quoted_string() -> None:
    """Verify Redact[T] works in a quoted annotation."""

    class Quoted(TypedException):
        token: "Redact[str]" = ""  # noqa: UP037

    assert Quoted(token="secret").context == {}
    assert Quoted.__redact__ == frozenset({"token"})


def test_redact_annotation_is_inherited() -> None:
    """Verify Redact[T] redaction is inherited."""

    class Parent(TypedException):
        token: Redact[str] = ""

    class Derived(Parent):
        pass

    assert Derived(token="secret").context == {}
    assert Derived.__redact__ == frozenset({"token"})


def test_redact_annotation_with_future_annotations() -> None:
    """Verify Redact[T] works when all annotations are stringized."""
    namespace: dict[str, Any] = {
        "TypedException": TypedException,
        "Redact": Redact,
    }
    exec(
        "from __future__ import annotations\n"
        "class FutureMarkers(TypedException):\n"
        "    token: Redact[str] = ''\n",
        namespace,
    )
    cls = cast(type[TypedException], namespace["FutureMarkers"])
    assert cls.__redact__ == frozenset({"token"})
    assert cls(token="secret").context == {}  # type: ignore[call-arg]


def test_redact_alias_name_fallback() -> None:
    """Verify Redact is recognized without a resolvable namespace."""
    namespace: dict[str, Any] = {
        "__name__": "unregistered_annotation_module",
        "TypedException": TypedException,
    }
    exec(
        "from __future__ import annotations\n"
        "class FallbackMarker(TypedException):\n"
        "    token: Redact[str] = ''\n",
        namespace,
    )
    cls = cast(type[TypedException], namespace["FallbackMarker"])
    assert cls.__redact__ == frozenset({"token"})
    assert cls(token="secret").context == {}  # type: ignore[call-arg]


def test_recursive_type_alias_annotation_terminates() -> None:
    """Verify recursive aliases do not loop during marker inspection."""
    namespace: dict[str, Any] = {
        "Annotated": Annotated,
        "TypedException": TypedException,
    }
    exec(
        "type Recursive = Annotated[Recursive, 'not-redacted']\n"
        "class RecursiveValue(TypedException):\n"
        "    value: Recursive = 1\n",
        namespace,
    )
    cls = cast(type[TypedException], namespace["RecursiveValue"])
    assert cls.__redact__ == frozenset()
    assert cls().context == {"value": 1}


def test_redact_local_alias_with_future_annotations_is_not_detected() -> None:
    """Pin the documented limit: local aliases under future annotations.

    The name cannot be resolved from the class or module namespace, so the
    marker is not detected. Security-sensitive fields should use
    ``field(redact=True)`` instead.
    """
    namespace: dict[str, Any] = {"TypedException": TypedException}
    exec(
        "from __future__ import annotations\n"
        "from typed_exceptions.exception import Redact as R\n"
        "class LocalAlias(TypedException):\n"
        "    token: R[str] = ''\n",
        namespace,
    )
    cls = cast(type[TypedException], namespace["LocalAlias"])
    assert cls.__redact__ == frozenset()
    assert cls(token="secret").context == {"token": "secret"}  # type: ignore[call-arg]

    class Explicit(TypedException):
        token: str = field(default="", redact=True)

    assert Explicit(token="secret").context == {}


# --- inheritance ---


def test_inheritance_three_levels() -> None:
    """Verify fields and templates inherit across three levels."""
    assert GrandChild.__field_names__ == {"message", "a", "b", "c", "secret"}
    error = GrandChild(a=1, c=2.0)
    assert str(error) == "grand 1 b 2.0"
    assert error.context == {"a": 1, "b": "b", "c": 2.0}


def test_child_inherits_parent_template() -> None:
    """Verify a child inherits its parent template."""
    assert str(Child(a=3)) == "base 3"


def test_redeclare_default() -> None:
    """Verify a subclass can replace an inherited default."""

    class NewDefault(Child):
        b: str = "new"

    assert NewDefault(a=1).b == "new"


def test_redeclare_as_required() -> None:
    """Verify a subclass can make an inherited field required."""

    # Allowed at runtime, unlike dataclasses; pyright follows dataclasses.
    class NowRequired(Child):
        b: str  # pyright: ignore[reportGeneralTypeIssues]

    with pytest.raises(TypeError, match="'b'"):
        NowRequired(a=1)  # type: ignore[call-arg]


def test_field_turned_into_class_var() -> None:
    """Verify a subclass can turn an inherited field into a ClassVar."""

    class Hidden(Child):
        b: ClassVar[str] = "constant"  # type: ignore[misc]

    assert "b" not in Hidden.__field_names__
    assert Hidden(a=1).context == {"a": 1}

    class FurtherHidden(Hidden):
        pass

    assert "b" not in FurtherHidden.__field_names__
    assert FurtherHidden(a=1).context == {"a": 1}


def test_context_preserves_declaration_order() -> None:
    """Verify context preserves field declaration order."""
    error = GrandChild(a=1)
    assert list(error.context) == ["a", "b", "c"]


def test_multiple_inheritance_merges_fields() -> None:
    """Verify multiple bases merge fields in reverse MRO order."""

    class Left(TypedException):
        message_template = "left {a}"
        a: int = 1

    class Right(TypedException):
        b: str = "b"

    class Combined(Left, Right):
        c: float = 1.5

    # Reverse MRO, like dataclasses: Right before Left.
    assert list(Combined().context) == ["b", "a", "c"]
    assert Combined().context == {"b": "b", "a": 1, "c": 1.5}


def test_extra_instance_attributes_are_allowed() -> None:
    """Verify unknown instance attributes are allowed but not in context."""
    error = UserNotFound(user_id=1)
    error.extra = "x"
    assert error.extra == "x"  # type: ignore[attr-defined]
    assert error.context == {"user_id": 1}


# --- class-level immutability ---


def test_class_attributes_are_frozen() -> None:
    """Verify compiled class attributes cannot be changed or deleted."""
    with pytest.raises(AttributeError, match="frozen class attribute"):
        UserNotFound.message_template = "changed"
    with pytest.raises(AttributeError, match="frozen class attribute"):
        del UserNotFound.__fields__


def test_unfrozen_class_attributes_can_be_deleted() -> None:
    """Verify ordinary class attributes are not frozen by the metaclass."""

    class WithTemporaryAttribute(TypedException):
        value: int = 0
        temporary = object()

    del WithTemporaryAttribute.temporary
    assert "temporary" not in vars(WithTemporaryAttribute)


def test_finalize_requires_message_field() -> None:
    """Verify field finalization rejects schemas without the message field."""
    with pytest.raises(TypeError, match="message.*instance field"):
        exception_module._finalize_fields(
            UserNotFound, {}, {}, set(), frozenset()
        )


def test_frozen_decorator_excludes_class_vars() -> None:
    """Verify frozen objects allow instance shadows of ClassVar attributes."""

    @frozen
    class FrozenValue:
        constant: ClassVar[int] = 1
        value: int = 2

    class FrozenValueChild(FrozenValue):
        pass

    instance = FrozenValueChild()
    setattr(instance, "constant", 3)
    assert getattr(instance, "constant") == 3
    with pytest.raises(AttributeError):
        instance.value = 4


def test_fields_mapping_is_read_only() -> None:
    """Verify the fields mapping is read-only."""
    fields = UserNotFound.__fields__
    assert list(fields) == ["message", "user_id"]
    with pytest.raises(TypeError):
        fields["x"] = fields["user_id"]  # type: ignore[index]


# --- pickle / copy ---


def test_pickle_roundtrip() -> None:
    """Verify pickling preserves fields, message, context, and notes."""
    error = GrandChild(a=1, secret="s")
    error.add_note("note")
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is GrandChild
    assert restored.args == error.args
    assert restored.message == error.message
    assert restored.context == error.context
    assert restored.secret == "s"
    assert restored.__notes__ == ["note"]


def test_pickle_restores_slot_fields_and_extra_attributes() -> None:
    """Verify pickle restores schema slots and arbitrary instance state."""
    error = SlottedFailure(trace_id="trace-1")
    error.extra = "extra"

    restored = pickle.loads(pickle.dumps(error))
    assert restored.trace_id == "trace-1"
    assert restored.extra == "extra"

    restored.__setstate__((None, {"trace_id": "trace-2"}))
    assert restored.trace_id == "trace-2"


def test_deepcopy_preserves_cycles() -> None:
    """Verify deepcopy preserves reference cycles."""
    error = ConfigWarning(name="cfg")
    error.tags.append(error)  # type: ignore[arg-type]
    clone = copy.deepcopy(error)
    assert clone is not error
    assert clone.tags[0] is clone


def test_copy() -> None:
    """Verify shallow copy preserves field values."""
    error = UserNotFound(user_id=1)
    assert copy.copy(error).context == {"user_id": 1}


def test_restore_legacy_state_argument() -> None:
    """Verify legacy pickle state is restored without __init__."""
    restored = exception_module._restore_exception(
        UserNotFound, ("legacy",), {"user_id": 5, "message": "legacy"}
    )
    assert restored.user_id == 5
    assert restored.message == "legacy"
