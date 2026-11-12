"""Annotation introspection for Python 3.14 class semantics."""

import sys
from annotationlib import Format, get_annotations
from ast import literal_eval
from types import ModuleType
from typing import Any, ClassVar, get_origin

from typed_exceptions.exception.sentinel import MISSING


def declared_annotations(cls: type) -> dict[str, Any]:
    """Return class annotations without evaluating forward references.

    Args:
        cls: Class whose annotations should be read.

    Returns:
        A mapping of attribute names to annotation objects or strings.
    """
    # PEP 649: __annotations__ can raise NameError for forward refs.
    return get_annotations(cls, format=Format.FORWARDREF)


_CLASSVAR_NAMES = frozenset(
    {
        "ClassVar",
        "typing.ClassVar",
        "typing_extensions.ClassVar",
    }
)


def is_classvar(annotation: object, owner: type) -> bool:
    """Return whether an annotation denotes ``ClassVar``.

    Args:
        annotation: Annotation to inspect.
        owner: Class that declares the annotation.

    Returns:
        ``True`` if the annotation is a ``ClassVar``, otherwise ``False``.
    """
    if annotation is ClassVar or get_origin(annotation) is ClassVar:
        return True
    if isinstance(annotation, str):
        return _classvar_from_string(annotation, owner)
    return False


def _classvar_from_string(annotation: str, owner: type) -> bool:
    # PEP 563 / PEP 649 string annotations must not be evaluated: a forward
    # reference may not be resolvable while __init_subclass__ is running.
    head = string_annotation_head(annotation)
    if head is None:
        return False
    value = resolve_qualified_name(head.split("."), owner)
    if value is not MISSING:
        return value is ClassVar
    return head in _CLASSVAR_NAMES


def string_annotation_head(annotation: str) -> str | None:
    """Return the dotted head name of a string annotation.

    The head is the part before any subscript, with all whitespace removed.
    Quoted annotations such as ``"'Item[int]'"`` are unquoted first.

    Args:
        annotation: String annotation to inspect.

    Returns:
        The normalized dotted name, or ``None`` when it is not a valid
        identifier chain.
    """
    if annotation.startswith(("'", '"')):
        unquoted = _unquote(annotation)
        if unquoted is None:
            return None
        annotation = unquoted
    name = "".join(annotation.split())
    head = name.partition("[")[0]
    if not all(part.isidentifier() for part in head.split(".")):
        return None
    return head


def resolve_string_annotation(annotation: str, owner: type) -> object | None:
    """Resolve the head of a string annotation in ``owner``'s namespaces.

    Args:
        annotation: String annotation to resolve.
        owner: Class that declares the annotation.

    Returns:
        The resolved object, or ``None`` when the name cannot be resolved.
    """
    head = string_annotation_head(annotation)
    if head is None:
        return None
    value = resolve_qualified_name(head.split("."), owner)
    return None if value is MISSING else value


def _unquote(annotation: str) -> str | None:
    try:
        unquoted: object = literal_eval(annotation)
    except SyntaxError, ValueError:
        return None
    if not isinstance(unquoted, str):
        return None
    return unquoted


def resolve_qualified_name(parts: list[str], owner: type) -> object:
    """Resolve a dotted name without evaluating descriptors.

    Args:
        parts: Identifier parts of the dotted name.
        owner: Class whose class and module namespaces are searched first.

    Returns:
        The resolved object, or ``MISSING`` when it cannot be found.
    """
    value = vars(owner).get(parts[0], MISSING)
    if value is MISSING:
        module = sys.modules.get(owner.__module__)
        if module is not None:
            value = vars(module).get(parts[0], MISSING)
    for part in parts[1:]:
        # Only read namespace dictionaries; do not invoke descriptors or
        # evaluate the annotation's possibly unresolved type argument.
        if not isinstance(value, (ModuleType, type)):
            return MISSING
        value = vars(value).get(part, MISSING)
    return value
