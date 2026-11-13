"""Compile and render exception message templates."""

import string
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from typed_exceptions.exception import TypedException

from typed_exceptions.access import frozen


@frozen
class DynamicTemplatePart:
    """One literal or field placeholder in a compiled template.

    Attributes:
        literal: Text emitted before the placeholder value.
        name: Field name to read, or ``None`` for a literal-only part.
    """

    __slots__ = (
        "literal",
        "name",
    )
    literal: str
    name: str | None

    def __init__(self, literal: str, name: str | None = None) -> None:
        """Initialize the template part.

        Args:
            literal: Text emitted before the placeholder value.
            name: Field name to read, or ``None`` for a literal-only part.
        """
        object.__setattr__(self, "literal", literal)
        object.__setattr__(self, "name", name)

    def build(self, owner: object) -> str:
        """Render this part using values from ``owner``.

        Args:
            owner: Object that provides the placeholder value.

        Returns:
            The rendered text for this part.
        """
        if self.name is None:
            return self.literal
        value = getattr(owner, self.name)
        try:
            text = str(value)
        except Exception:
            # A broken __str__ must not replace the error being raised.
            text = f"<unprintable {type(value).__name__} object>"
        return self.literal + text

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        """Rebuild the part through its constructor (see ``Field``)."""
        return type(self), (self.literal, self.name)


@frozen
class DynamicTemplate:
    """Immutable sequence of compiled template parts.

    Attributes:
        parts: Compiled template parts in render order.
        names: Field names used by the placeholders.
    """

    __slots__ = ("parts", "names")
    parts: tuple[DynamicTemplatePart, ...]
    names: frozenset[str]

    def __init__(self, *parts: DynamicTemplatePart) -> None:
        """Initialize the template from its parts.

        Args:
            *parts: Compiled template parts in render order.
        """
        object.__setattr__(self, "parts", parts)
        object.__setattr__(
            self,
            "names",
            frozenset(part.name for part in parts if part.name is not None),
        )

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        """Rebuild the template through its constructor (see ``Field``)."""
        return type(self), self.parts

    @property
    def is_static(self) -> bool:
        """Return whether the template has no placeholders."""
        return not self.names

    def build(self, owner: object) -> str:
        """Render the full template using values from ``owner``.

        Args:
            owner: Object that provides the placeholder values.

        Returns:
            The rendered message.
        """
        return "".join(part.build(owner) for part in self.parts)


class TemplateCompiler:
    """Compile message templates into literal and placeholder parts."""

    formatter = string.Formatter()

    @classmethod
    def compile(
        cls,
        error_type: type[TypedException],
        template: str,
        field_names: frozenset[str],
        redacted: frozenset[str],
    ) -> tuple[tuple[str, str | None], ...]:
        """Compile a template and validate its placeholders.

        Args:
            error_type: Exception class used in error messages.
            template: Message template to compile.
            field_names: Names of fields that may appear in the template.
            redacted: Names of fields that must not appear in the template.

        Returns:
            Pairs of literal text and optional field name.

        Raises:
            ValueError: If a placeholder is unsupported, unknown, or redacted.
        """
        result: list[tuple[str, str | None]] = []

        try:
            for literal, name, format_spec, conversion in cls.formatter.parse(
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


TEMPLATE_COMPILER: Final[TemplateCompiler] = TemplateCompiler()
