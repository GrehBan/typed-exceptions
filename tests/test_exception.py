"""Tests for TypedException fields, templates, and runtime behavior."""

import abc
import copy
import dataclasses
import doctest
import pickle
import sys
import types
import typing
from collections.abc import Callable
from typing import Annotated, Any, ClassVar, Final, Literal, cast

import pytest

from typed_exceptions import (
    MISSING,
    Field,
    Redact,
    TypedException,
    field,
)
from typed_exceptions import exception as exception_module
from typed_exceptions.access import should_redact
from typed_exceptions.exception import FrozenClassAttrsMeta
from typed_exceptions.introspection import string_annotation_head
from typed_exceptions.snapshot import snapshot_exception


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
        spec.default = 2  # type: ignore[misc]
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


def test_message_is_rerendered_when_a_template_field_changes() -> None:
    """Verify a template message follows the fields it uses."""
    error = UserNotFound(user_id=1)
    error.user_id = 2
    assert str(error) == "User 2 was not found"
    assert error.message == "User 2 was not found"
    assert error.args == ("User 2 was not found",)
    assert error.context == {"user_id": 2}


class Ticket(TypedException):
    """Exception with a template field and a field outside the template."""

    message_template = "ticket {number}"
    number: int
    owner: str = "nobody"


def test_rerender_ignores_fields_outside_the_template() -> None:
    """Verify only placeholder fields trigger a new rendering."""
    error = Ticket(number=1)
    error.message = "manual"
    error.owner = "bob"
    assert str(error) == "manual"
    fresh = Ticket(number=1)
    fresh.owner = "bob"
    fresh.extra = "x"
    assert str(fresh) == "ticket 1"


def test_literal_message_is_never_rerendered() -> None:
    """Verify ``message=`` set at construction stays literal."""
    error = Ticket(number=1, message="custom {number}")
    error.number = 2
    assert str(error) == "custom {number}"
    assert error.context["number"] == 2


def test_message_assignment_makes_the_message_literal() -> None:
    """Verify assigning ``message`` or ``args`` stops following fields."""
    error = Ticket(number=1)
    error.message = "manual"
    error.number = 2
    assert str(error) == "manual"
    other = Ticket(number=1)
    other.args = ("by args",)
    other.number = 2
    assert str(other) == "by args"
    assert "__live_message__" not in vars(other)


def test_message_missing_follows_the_template() -> None:
    """Verify ``message=MISSING`` behaves like an omitted message."""
    error = Ticket(number=1, message=MISSING)  # type: ignore[arg-type]
    error.number = 3
    assert str(error) == "ticket 3"


def test_instance_template_is_rerendered_with_its_own_template() -> None:
    """Verify an instance template keeps following the fields."""
    error = Ticket(number=1, message_template="#{number} of {owner}")
    error.number = 2
    error.owner = "bob"
    assert str(error) == "#2 of bob"
    assert Ticket.message_template == "ticket {number}"


def test_static_message_keeps_no_extra_instance_state() -> None:
    """Verify exceptions without placeholders carry no rendering state."""

    class Static(TypedException):
        message_template = "always"
        value: int = 0

    error = Static()
    error.value = 5
    assert str(error) == "always"
    assert "__live_message__" not in vars(error)
    assert "__live_message__" not in vars(Static(message="x"))


def test_rerender_uses_the_value_normalized_by_custom_setattr() -> None:
    """Verify a subclass ``__setattr__`` runs before the re-rendering."""

    class Normalized(TypedException):
        message_template = "value={value}"
        value: int

        def __setattr__(self, name: str, value: Any) -> None:
            if name == "value":
                value += 1
            super().__setattr__(name, value)

    error = Normalized(value=1)
    assert str(error) == "value=2"
    error.value = 10
    assert str(error) == "value=11"


def test_rerender_survives_pickle_and_copy() -> None:
    """Verify restored exceptions keep following their fields."""
    for template in (None, "no. {number}"):
        error = (
            Ticket(number=1)
            if template is None
            else Ticket(number=1, message_template=template)
        )
        expected = "ticket 2" if template is None else "no. 2"
        for clone in (
            pickle.loads(pickle.dumps(error)),
            copy.copy(error),
            copy.deepcopy(error),
        ):
            clone.number = 2
            assert str(clone) == expected
            assert clone.args == (expected,)
            assert str(error) != expected


def test_literal_message_stays_literal_after_pickle() -> None:
    """Verify a literal message does not start following fields."""
    error = Ticket(number=1, message="fixed")
    clone = pickle.loads(pickle.dumps(error))
    clone.number = 5
    assert str(clone) == "fixed"


def test_rerender_tolerates_unprintable_values() -> None:
    """Verify a failing ``__str__`` on assignment does not raise."""

    class Broken:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    error = Ticket(number=1)
    error.number = Broken()  # type: ignore[assignment]
    name = f"{Broken.__module__}.{Broken.__qualname__}"
    assert str(error) == f"ticket <unprintable {name}>"


def test_in_place_mutation_does_not_rerender() -> None:
    """Pin the documented limit: in-place changes are not detected."""

    class Tagged(TypedException):
        message_template = "tags={tags}"
        tags: list[str] = field(factory=list)

    error = Tagged()
    error.tags.append("x")
    assert str(error) == "tags=[]"
    error.tags = ["y"]
    assert str(error) == "tags=['y']"


def test_unprintable_field_value_does_not_replace_the_error() -> None:
    """Verify a failing ``__str__`` is replaced by a placeholder."""

    class Broken:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    class WithObject(TypedException):
        message_template = "object {obj}"
        obj: object

    error = WithObject(obj=Broken())
    name = f"{Broken.__module__}.{Broken.__qualname__}"
    assert str(error) == f"object <unprintable {name}>"
    assert isinstance(error.obj, Broken)


def test_annotated_message_template_is_not_a_field() -> None:
    """Verify ``message_template: str = ...`` stays a template."""

    class Annotated_(TypedException):
        message_template: str = "item {item_id}"
        item_id: int

    error = Annotated_(item_id=3)
    assert str(error) == "item 3"
    assert error.context == {"item_id": 3}
    assert "message_template" not in Annotated_.__field_names__
    other = Annotated_(item_id=3, message_template="other {item_id}")
    assert str(other) == "other 3"


class Layered(TypedException):
    """Exception used to exercise per-instance templates."""

    message_template = "Failure in {layer}"
    layer: str
    secret: Redact[str] = "s"


def test_instance_message_template_is_rendered() -> None:
    """Verify ``message_template=`` renders for a single instance."""
    error = Layered(layer="cache", message_template="Cache {layer} down")
    assert str(error) == "Cache cache down"
    assert error.message == "Cache cache down"
    assert error.args == ("Cache cache down",)
    assert error.context == {"layer": "cache"}
    assert str(Layered(layer="db")) == "Failure in db"
    assert Layered.message_template == "Failure in {layer}"


def test_instance_message_template_supports_escaped_braces() -> None:
    """Verify doubled braces stay literal in an instance template."""
    error = Layered(layer="x", message_template="{{raw}} {layer}")
    assert str(error) == "{raw} x"


def test_instance_message_template_missing_is_unset() -> None:
    """Verify ``MISSING`` falls back to the class template."""
    error = Layered(
        layer="db",
        message_template=MISSING,  # type: ignore[arg-type]
    )
    assert str(error) == "Failure in db"
    error = Layered(
        layer="db",
        message=MISSING,  # type: ignore[arg-type]
        message_template="{layer}!",
    )
    assert str(error) == "db!"


def test_instance_message_template_conflicts_with_message() -> None:
    """Verify a literal message and an instance template are exclusive."""
    with pytest.raises(TypeError, match="either message or message_template"):
        Layered(layer="a", message="m", message_template="{layer}")


def test_instance_message_template_must_be_str() -> None:
    """Verify a non-string instance template is rejected."""
    with pytest.raises(TypeError, match="message_template must be str"):
        Layered(layer="a", message_template=1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{nope}", "unknown placeholder 'nope'"),
        ("{message}", "unknown placeholder 'message'"),
        ("{secret}", "redacted field 'secret'"),
        ("{layer:>5}", "unsupported placeholder"),
        ("{layer!r}", "unsupported placeholder"),
        ("{layer.x}", "unsupported placeholder"),
        ("{}", "unsupported placeholder"),
    ],
)
def test_instance_message_template_is_validated(
    template: str, expected: str
) -> None:
    """Verify instance templates use the same strict placeholder rules."""
    with pytest.raises(ValueError, match=expected) as info:
        Layered(layer="a", message_template=template)
    assert str(info.value).startswith("Layered.message_template: ")


def test_instance_message_template_survives_pickle_and_copy() -> None:
    """Verify the rendered instance message is what gets restored."""
    error = Layered(layer="q", message_template="Queue {layer} full")
    for clone in (
        pickle.loads(pickle.dumps(error)),
        copy.copy(error),
        copy.deepcopy(error),
    ):
        assert str(clone) == "Queue q full"
        assert clone.context == {"layer": "q"}


def test_instance_message_template_compilation_is_cached() -> None:
    """Verify equal instance templates are compiled once per class."""
    first = exception_module._instance_template(Layered, "x {layer}")
    second = exception_module._instance_template(Layered, "x {layer}")
    assert first is second


def test_instance_message_template_is_not_a_context_field() -> None:
    """Verify the keyword never becomes a field or leaks into context."""
    assert "message_template" not in Layered.__field_names__
    error = Layered(layer="a", message_template="{layer}")
    assert "message_template" not in error.context
    assert "message_template" not in vars(error)


def test_annotated_template_forms_work_at_runtime() -> None:
    """Verify ``ClassVar`` and ``Final`` templates still work at runtime.

    mypy rejects both forms (README, "Type checking"). The ignore comments
    pin that limit: if mypy stops reporting it, ``warn_unused_ignores``
    flags them and the documentation has to be revisited.
    """

    class WithClassVar(TypedException):
        message_template: ClassVar[str] = "classvar {n}"  # type: ignore[misc]
        n: int

    class WithFinal(TypedException):
        message_template: Final = "final {n}"  # type: ignore[misc]
        n: int

    assert str(WithClassVar(n=1)) == "classvar 1"
    assert str(WithFinal(n=2)) == "final 2"
    assert WithClassVar.__field_names__ == {"message", "n"}
    assert WithFinal.__field_names__ == {"message", "n"}


def test_unannotated_template_override_is_type_checked_cleanly() -> None:
    """Verify the documented form passes mypy, kwarg included."""

    class Plain(Layered):
        message_template = "plain {layer}"

    assert str(Plain(layer="x")) == "plain x"
    assert str(Plain(layer="x", message_template="own {layer}")) == "own x"


def test_template_is_frozen_on_inheriting_subclasses() -> None:
    """Verify a subclass cannot silently shadow the compiled template."""

    class Parent(TypedException):
        message_template = "parent"

    class Child(Parent):
        pass

    with pytest.raises(AttributeError, match="frozen class attribute"):
        Child.message_template = "changed"
    with pytest.raises(AttributeError, match="frozen class attribute"):
        del Child.message_template
    with pytest.raises(AttributeError, match="frozen class attribute"):
        Child.message = "changed"
    assert str(Child()) == "parent"
    assert Child.message_template == "parent"


def test_other_metaclasses_conflict_unless_combined() -> None:
    """Pin the documented metaclass limit and its workaround."""
    with pytest.raises(TypeError, match="metaclass conflict"):

        class Plain(TypedException, abc.ABC):  # type: ignore[metaclass]
            message_template = "x"

    class Meta(FrozenClassAttrsMeta, abc.ABCMeta):
        pass

    class Combined(TypedException, metaclass=Meta):
        message_template = "combined {n}"
        n: int

    assert str(Combined(n=1)) == "combined 1"
    with pytest.raises(AttributeError, match="frozen class attribute"):
        Combined.message_template = "changed"


_FORMS_SOURCE = """
from typing import Annotated, Literal, Optional, Union

from typed_exceptions import TypedException, Redact, field

type Secret = Redact[str]
type Json = dict[str, "Json"] | list["Json"] | Redact[str]
type Plain = list["Plain"] | int


class Forms(TypedException):
    message_template = "forms"
    direct: Redact[str] = ""
    pep604: Redact[str] | None = None
    reversed_: None | Redact[str] = None
    optional: Optional[Redact[str]] = None
    union: Union[int, Redact[str]] = 0
    inner: Redact[str | None] = None
    alias: Secret = ""
    alias_optional: Secret | None = None
    annotated_union: Annotated[Redact[str], "meta"] | None = None
    in_list: list[Redact[str]] = field(factory=list)
    in_dict: dict[str, Redact[str]] = field(factory=dict)
    in_tuple: tuple[Redact[str], ...] = ()
    quoted_inner: list["Redact[str]"] = field(factory=list)
    recursive_json: Json = ""
    plain: str = ""
    annotated_plain: Annotated[str, "meta"] | None = None
    literal: Literal["Redact"] = "Redact"
    literal_union: Literal["Redact"] | None = None
    recursive_plain: Plain = 0
    optional_plain: Optional[int] = None
"""
_REDACTED_FORMS = frozenset(
    {
        "direct",
        "pep604",
        "reversed_",
        "optional",
        "union",
        "inner",
        "alias",
        "alias_optional",
        "annotated_union",
        "in_list",
        "in_dict",
        "in_tuple",
        "quoted_inner",
        "recursive_json",
    }
)
_VISIBLE_FORMS = frozenset(
    {
        "plain",
        "annotated_plain",
        "literal",
        "literal_union",
        "recursive_plain",
        "optional_plain",
    }
)


def _load_module(
    monkeypatch: pytest.MonkeyPatch, name: str, source: str
) -> types.ModuleType:
    module = types.ModuleType(name)
    monkeypatch.setitem(sys.modules, name, module)
    exec(source, module.__dict__)
    return module


@pytest.mark.parametrize(
    "prefix",
    ["", "from __future__ import annotations\n"],
    ids=["eager", "future"],
)
def test_redact_is_detected_in_every_annotation_form(
    prefix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Redact hides a field wherever it appears in the annotation.

    Unions, ``Optional``, generic arguments, aliases, and quoted parts must
    behave the same with and without postponed annotations.
    """
    module = _load_module(
        monkeypatch, f"redact_forms_{len(prefix)}", prefix + _FORMS_SOURCE
    )
    forms = module.Forms
    assert forms.__redact__ == _REDACTED_FORMS
    assert frozenset(forms().context) == _VISIBLE_FORMS


def test_redact_is_detected_through_unresolved_forward_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify names that are defined later do not hide the marker.

    PEP 649 replaces the whole annotation with a forward reference, which
    keeps the parts that did resolve in a separate mapping.
    """
    module = _load_module(
        monkeypatch,
        "redact_later",
        """
from typing import Optional

from typed_exceptions import TypedException, Redact


class Holder(TypedException):
    message_template = "holder"
    a: Redact[str] | Later = ""
    b: "Redact[str] | Later" = ""
    c: "int | Later" = 0
    d: list[Redact[str] | Later] = None
    e: Redact[Later] = None
    f: Later | None = None
    g: Optional[Redact[Later]] = None
    h: Later = None


class Later:
    pass
""",
    )
    holder = module.Holder
    assert holder.__redact__ == {"a", "b", "d", "e", "g"}
    assert frozenset(holder().context) == {"c", "f", "h"}


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (Callable[[Redact[str]], int], True),
        ("'Redact[str]'", True),
        ("typed_exceptions.access.Redact[str]", True),
        ("list[", False),
        ("a\x00b", False),
        ("[" * 3000, False),
        (Literal["Redact"], False),
        ("Literal['Redact']", False),
        ("typing.Literal['Redact'] | None", False),
        ("int | None", False),
    ],
)
def test_should_redact_edge_cases(annotation: object, expected: bool) -> None:
    """Verify odd, invalid, and look-alike annotations are handled safely."""
    assert should_redact(annotation, UserNotFound) is expected


def test_marker_lookup_does_not_call_equality_of_other_metadata() -> None:
    """Verify unrelated ``Annotated`` metadata is compared by identity."""

    class Unequal:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("must not be called")

        __hash__ = None  # type: ignore[assignment]

    assert should_redact(Annotated[str, Unequal()], UserNotFound) is False


def test_reinitializing_a_live_exception_with_a_literal_message() -> None:
    """Verify a second ``__init__`` does not keep the old template."""
    error = Ticket(number=1)
    error.__init__(number=2, message="literal")  # type: ignore[misc]
    error.number = 3
    assert str(error) == "literal"
    assert "__live_message__" not in vars(error)


def test_reinitializing_a_literal_exception_without_a_message() -> None:
    """Verify a second ``__init__`` can make the message live again."""
    error = Ticket(number=1, message="literal")
    error.__init__(number=2)  # type: ignore[misc]
    error.number = 3
    assert str(error) == "ticket 3"


def test_field_named_self_is_supported() -> None:
    """Verify ``self`` and ``cls`` are usable as field names."""

    class Odd(TypedException):
        message_template = "{self}/{cls}"
        self: int
        cls: int

    error = Odd(self=1, cls=2)
    assert str(error) == "1/2"
    assert error.context == {"self": 1, "cls": 2}
    assert copy.deepcopy(error).context == {"self": 1, "cls": 2}


class Spec(TypedException):
    """Exception whose compiled metadata is copied in the tests below."""

    message_template = "{a}-tail"
    a: int
    b: Redact[str] = field(default="x", reserved=True)
    c: list[int] = field(factory=list)


def _clones(value: Any) -> list[Any]:
    return [
        copy.copy(value),
        copy.deepcopy(value),
        pickle.loads(pickle.dumps(value)),
    ]


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_field_survives_copy_and_pickle(name: str) -> None:
    """Verify frozen ``Field`` objects can be copied and pickled."""
    original = Spec.__fields__[name]
    for clone in _clones(original):
        assert type(clone) is Field
        assert clone is not original
        assert repr(clone) == repr(original)
        with pytest.raises(AttributeError):
            clone.name = "changed"  # type: ignore[misc]


def test_template_survives_copy_and_pickle() -> None:
    """Verify a compiled template and its parts stay functional."""
    template = Spec.__dynamic_template__
    for clone in _clones(template):
        assert clone is not template
        assert clone.names == template.names == {"a"}
        assert clone.is_static is template.is_static is False
        assert clone.build(Spec(a=9)) == "9-tail"
        assert [(p.literal, p.name) for p in clone.parts] == [
            (p.literal, p.name) for p in template.parts
        ]
        with pytest.raises(AttributeError):
            clone.parts = ()
    part = template.parts[0]
    for part_clone in _clones(part):
        assert part_clone.literal == part.literal
        assert part_clone.name == part.name


def test_field_spec_compares_and_hashes_by_value() -> None:
    """Verify equal field specifications are interchangeable."""
    first = Field[int](default=1, name="a")
    assert first == Field[int](default=1, name="a")
    assert hash(first) == hash(Field[int](default=1, name="a"))
    assert first != Field[int](default=2, name="a")
    assert first != Field[int](default=1, name="b")
    assert len({first, Field[int](default=1, name="a")}) == 1


def test_field_spec_positional_order_is_stable() -> None:
    """Verify the positional constructor order of ``Field`` is preserved."""
    spec = Field(1, MISSING, True, True, "x")
    assert (spec.default, spec.factory) == (1, MISSING)
    assert (spec.redact, spec.reserved, spec.name) == (True, True, "x")


def test_dynamic_template_derives_names_from_parts() -> None:
    """Verify ``names`` follows ``parts`` and the empty template is static."""
    from typed_exceptions.template import DynamicTemplate, DynamicTemplatePart

    empty = DynamicTemplate()
    assert empty.parts == ()
    assert empty.names == frozenset()
    assert empty.is_static

    template = DynamicTemplate(
        (DynamicTemplatePart("a "), DynamicTemplatePart(" b", "x"))
    )
    assert template.names == {"x"}
    assert template == DynamicTemplate(template.parts)
    assert hash(template) == hash(DynamicTemplate(template.parts))
    with pytest.raises(AttributeError):
        template.names = frozenset()  # type: ignore[misc]


def test_exception_snapshots_are_frozen_values() -> None:
    """Verify snapshot objects are immutable and compare by value."""
    from typed_exceptions.snapshot import FrameSnapshot

    frame = FrameSnapshot("f.py", 1, "run", "x = 1", None)
    assert frame == FrameSnapshot("f.py", 1, "run", "x = 1", None)
    with pytest.raises(AttributeError):
        frame.name = "other"  # type: ignore[misc]

    chain = UserNotFound(user_id=1).snapshot()
    assert chain.items[0].type.endswith("UserNotFound")
    with pytest.raises(AttributeError):
        chain.schema_version = "2"  # type: ignore[misc]
    assert chain.to_dict()["root"] == 0


class _SecretHolder(TypedException):
    message_template = "secret holder"
    token: Redact[str]


def _raise_secret_holder(token: str) -> None:
    password = "hunter2"
    assert password
    raise _SecretHolder(token=token)


def test_snapshot_to_json_survives_unserializable_context() -> None:
    """Verify ``to_json`` stringifies values JSON cannot encode."""
    import datetime
    import json
    import uuid

    class WithValues(TypedException):
        message_template = "values"
        when: datetime.datetime
        ident: uuid.UUID

    error = WithValues(
        when=datetime.datetime(2026, 1, 2), ident=uuid.UUID(int=1)
    )
    context = json.loads(error.snapshot().to_json())["exceptions"][0][
        "context"
    ]
    assert context == {
        "when": "2026-01-02 00:00:00",
        "ident": str(uuid.UUID(int=1)),
    }


def test_snapshot_locals_hide_redacted_names() -> None:
    """Verify captured locals do not leak a redacted field by name."""
    # Built at runtime so the literal is not echoed back by ``line_text``.
    token = "".join(("s3", "cr3t"))
    try:
        _raise_secret_holder(token)
    except _SecretHolder as error:
        chain = error.snapshot(capture_locals=True)

    frames = {frame.name: frame for frame in chain.items[0].trace}
    local = frames["_raise_secret_holder"].locals
    assert local is not None
    assert local["token"] == "<redacted>"
    assert local["password"] == "'hunter2'"
    assert "s3cr3t" not in chain.to_json()


def test_snapshot_locals_are_clipped() -> None:
    """Verify very long local reprs are clipped to a fixed length."""
    from typed_exceptions.snapshot import (
        MAX_LOCAL_LENGTH,
        snapshot_exception,
    )

    def boom(blob: str) -> None:
        raise ValueError(blob[:1])

    try:
        boom("a" * 1000)
    except ValueError as error:
        chain = snapshot_exception(
            error, schema_version="1", capture_locals=True
        )

    local = chain.items[0].trace[-1].locals
    assert local is not None
    assert len(local["blob"]) == MAX_LOCAL_LENGTH
    assert local["blob"].endswith("\u2026")


def test_snapshot_reports_truncation() -> None:
    """Verify ``truncated`` tells a cut chain from a complete one."""
    from typed_exceptions.snapshot import snapshot_exception

    inner = ValueError("inner")
    outer = KeyError("outer")
    outer.__cause__ = inner

    assert snapshot_exception(outer, schema_version="1").truncated is False
    exact = snapshot_exception(outer, schema_version="1", limit=2)
    assert exact.truncated is False
    assert len(exact.items) == 2

    cut = snapshot_exception(outer, schema_version="1", limit=1)
    assert cut.truncated is True
    assert len(cut.items) == 1
    assert cut.items[0].cause_id is None
    assert cut.to_dict()["truncated"] is True

    none = snapshot_exception(outer, schema_version="1", limit=0)
    assert none.items == ()
    assert none.truncated is True


def test_snapshot_redacted_names_are_sorted() -> None:
    """Verify the redacted names come out in a stable order."""

    class Many(TypedException):
        message_template = "many"
        b: Redact[str] = field(default="")
        a: Redact[str] = field(default="")
        c: Redact[str] = field(default="")

    assert Many().snapshot().items[0].redacted == ("a", "b", "c")


def test_snapshot_tells_typed_from_plain_exceptions() -> None:
    """Verify no redactions (``[]``) differs from not typed (``None``)."""
    from typed_exceptions.snapshot import snapshot_exception

    typed = UserNotFound(user_id=1).snapshot().to_dict()["exceptions"][0]
    assert typed["redacted"] == []
    assert typed["context"] == {"user_id": 1}

    plain = snapshot_exception(ValueError("x"), schema_version="1")
    entry = plain.to_dict()["exceptions"][0]
    assert entry["redacted"] is None
    assert entry["context"] is None


def test_snapshot_notes_tolerate_odd_values() -> None:
    """Verify malformed ``__notes__`` cannot break a snapshot."""
    from typed_exceptions.snapshot import snapshot_exception

    error = ValueError("x")
    setattr(error, "__notes__", ["a", 1])
    assert snapshot_exception(error, schema_version="1").items[0].notes == (
        "a",
        "1",
    )
    setattr(error, "__notes__", "abc")
    assert snapshot_exception(error, schema_version="1").items[0].notes == ()


def test_snapshot_context_is_reduced_to_json_types() -> None:
    """Verify captured context holds plain JSON values only."""
    import datetime
    import json
    import uuid

    class Odd:
        def __str__(self) -> str:
            return "odd"

    class Rich(TypedException):
        message_template = "rich"
        payload: object

    payload = {
        "when": datetime.datetime(2026, 1, 2),
        "ids": (uuid.UUID(int=1), 2),
        "tags": {"b", "a"},
        3: Odd(),
        "nan": float("nan"),
        "inf": float("inf"),
        "ok": 1.5,
    }
    context = Rich(payload=payload).snapshot().items[0].context
    assert context == {
        "payload": {
            "when": "2026-01-02 00:00:00",
            "ids": [str(uuid.UUID(int=1)), 2],
            "tags": ["a", "b"],
            "3": "odd",
            "nan": "nan",
            "inf": "inf",
            "ok": 1.5,
        }
    }
    assert json.dumps(context, allow_nan=False)


def test_snapshot_context_is_detached_from_live_objects() -> None:
    """Verify later changes to a field do not alter the snapshot."""

    class Holder(TypedException):
        message_template = "holder"
        items: list[int]

    error = Holder(items=[1, 2])
    chain = error.snapshot()
    error.items.append(3)
    error.items = [9]
    context = chain.items[0].context
    assert context == {"items": [1, 2]}
    assert chain.to_dict()["exceptions"][0]["context"] is context


def test_snapshot_context_survives_hostile_values() -> None:
    """Verify cycles, deep nesting and broken containers do not raise."""

    class BrokenMapping(dict[str, int]):
        def items(self) -> typing.Any:
            raise RuntimeError("boom")

    class Hostile(TypedException):
        message_template = "hostile"
        value: object

    loop: list[object] = []
    loop.append(loop)
    deep: object = 0
    for _ in range(50):
        deep = [deep]

    for value in (loop, deep, BrokenMapping(a=1)):
        chain = Hostile(value=value).snapshot()
        assert chain.items[0].context is not None
        assert chain.to_json()


def test_public_api_is_exported_from_package_root() -> None:
    """Verify the package root re-exports the public API."""
    import typed_exceptions

    assert typed_exceptions.TypedException is TypedException
    assert typed_exceptions.Field is Field
    assert typed_exceptions.Redact is Redact
    assert typed_exceptions.MISSING is MISSING
    assert typed_exceptions.field is field
    assert typed_exceptions.snapshot_exception is snapshot_exception
    assert set(typed_exceptions.__all__) == {
        "MISSING",
        "Field",
        "Redact",
        "TypedException",
        "field",
        "snapshot_exception",
    }


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
        "from typed_exceptions import Redact as R\n"
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


def test_missing_marker_counts_as_not_supplied() -> None:
    """Verify an explicit MISSING never satisfies a required field."""

    class Needs(TypedException):
        message_template = "{n}"
        n: int
        m: int = 1

    with pytest.raises(TypeError, match="missing required"):
        Needs(n=MISSING)  # type: ignore[arg-type]
    assert Needs(n=2, m=MISSING).m == 1  # type: ignore[arg-type]


def test_default_with_mutable_item_is_rejected() -> None:
    """Verify hashable containers holding mutable items are rejected."""
    with pytest.raises(ValueError, match="unhashable default"):

        class Shared(TypedException):
            message_template = "x"
            items: tuple[list[int], ...] = ([],)


def test_redacting_message_is_rejected() -> None:
    """Verify __redact__ cannot name the message field."""
    with pytest.raises(TypeError, match="'message' cannot be listed"):

        class Hidden(TypedException):
            message_template = "x"
            __redact__ = frozenset({"message"})


def test_instance_template_cache_does_not_keep_classes_alive() -> None:
    """Verify dynamically created classes are still collected."""
    import gc
    import weakref

    class Temp(TypedException):
        message_template = "{a}"
        a: int

    Temp(a=1, message_template="t {a}")
    ref = weakref.ref(Temp)
    del Temp
    gc.collect()
    assert ref() is None


def test_deleted_template_field_does_not_break_other_assignments() -> None:
    """Verify assigning another field after deleting a placeholder works."""

    class Pair(TypedException):
        message_template = "{a}/{b}"
        a: int
        b: int

    error = Pair(a=1, b=2)
    del error.a
    error.b = 3
    assert error.b == 3
    assert error.message == "1/2"
