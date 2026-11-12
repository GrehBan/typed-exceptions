# typed-exceptions

English | [Русский](README.ru.md)

Typed, structured exceptions with message templates for Python 3.14+.

`typed-exceptions` provides a `TypedException` base class, imported from
`typed_exceptions`, that behaves like a normal Python exception while making
error data part of the exception itself: typed fields, keyword-only
construction, structured context, and readable message templates.

## Table of contents

- [Why typed-exceptions?](#why-typed-exceptions)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Defining exceptions](#defining-exceptions)
- [Constructor contract](#constructor-contract)
- [Defaults and factories](#defaults-and-factories)
- [Message templates](#message-templates)
- [Message overrides](#message-overrides)
- [Structured context](#structured-context)
- [Redaction](#redaction)
- [Reserved fields](#reserved-fields)
- [Inheritance](#inheritance)
- [Runtime behavior](#runtime-behavior)
- [Introspection](#introspection)
- [Pickling, copying, and notes](#pickling-copying-and-notes)
- [Type checking](#type-checking)
- [API reference](#api-reference)
- [Error reference](#error-reference)
- [Design notes and limitations](#design-notes-and-limitations)
- [Development](#development)
- [Project layout](#project-layout)
- [License](#license)

## Why typed-exceptions?

Plain exceptions usually carry either a formatted string or a dictionary
attached after construction. That makes error data hard to discover, easy to
typo, and awkward to consume programmatically.

`TypedException` makes the data part of the exception class:

- Fields are declared with normal type annotations.
- Construction is keyword-only and validates required fields at runtime.
- The message can be rendered from a template or overridden literally.
- Non-sensitive fields are exposed as a structured `context` mapping.
- Sensitive fields can be redacted.
- Static type checkers understand the constructor and field types.

## Features

- Typed, annotated context fields on exception subclasses
- Keyword-only constructors with runtime presence checks
- Required fields, defaults, and default factories
- Compatibility with `dataclasses.field`
- Message templates with simple `{field_name}` placeholders
- Literal message overrides
- Structured `context` mapping in declaration order
- Redaction via `field(redact=True)`, dataclass metadata, `Redact[T]`, or
  `__redact__`
- Reserved fields that stay accessible but are hidden from `context`
- Inheritance, field overriding, and inherited templates
- Immutable compiled class metadata and read-only field specifications
- Pickle, shallow copy, deep copy, and exception notes
- Static type checker support via `dataclass_transform`
- No runtime validation of field value types

## Requirements

- Python 3.14 or newer
- [`typing-extensions`](https://pypi.org/project/typing-extensions/)
  (`>=4.16.0`, installed automatically)

The implementation uses Python 3.14 features such as PEP 695 generics and type
aliases, PEP 649 annotations, and `typing_extensions.Sentinel`. Older Python
versions are not supported.

## Installation

With `uv`:

```bash
uv sync --all-groups
```

Or with `pip` from the project root:

```bash
pip install .
```

`uv sync` without `--all-groups` installs only the `dev` dependency group; the
`test` group is separate.

## Quick start

```python
from typed_exceptions.exception import TypedException


class UserNotFound(TypedException):
    message_template = "User {user_id} was not found"
    user_id: int


error = UserNotFound(user_id=42)

print(str(error))        # User 42 was not found
print(error.message)     # User 42 was not found
print(error.args)        # ('User 42 was not found',)
print(error.context)     # {'user_id': 42}
print(error.user_id)     # 42
```

`TypedException` is a regular `Exception` subclass, so it can be raised and
caught normally:

```python
try:
    raise UserNotFound(user_id=42)
except UserNotFound as error:
    print(error.context["user_id"])  # 42
```

## Defining exceptions

Subclass `TypedException` and declare fields as annotated attributes. Use
`message_template` to define how the message is rendered.

```python
from typed_exceptions.exception import TypedException


class InvalidToken(TypedException):
    message_template = "Token {token_id} is invalid for {scope}"
    token_id: str
    scope: str
```

Fields without a default are required. Fields with a default or a factory are
optional.

```python
InvalidToken(token_id="abc", scope="admin")  # OK
InvalidToken(scope="admin")                  # TypeError: missing 'token_id'
```

The constructor is keyword-only. Positional arguments are rejected, and unknown
keyword arguments raise `TypeError` with a sorted list of names.

```python
InvalidToken("abc", "admin")          # TypeError
InvalidToken(token_id="a", scope="b", extra=1)  # TypeError
```

`ClassVar` annotations are not fields:

```python
from typing import ClassVar

from typed_exceptions.exception import TypedException


class WithClassVar(TypedException):
    code: ClassVar[int] = 404
    detail: str = "x"


print(WithClassVar().context)  # {'detail': 'x'}
```

Use `ClassVar[T]` to exclude an annotated attribute from the field schema while
keeping the attribute accessible:

```python
from typing import ClassVar

from typed_exceptions.exception import TypedException


class WithConstant(TypedException):
    internal: ClassVar[int] = 0
    visible: int = 1


error = WithConstant()
print(error.internal)   # 0
print(error.context)    # {'visible': 1}
```

`ClassVar[T]` can also remove a field inherited from a base class. It is
understood by both the runtime and static type checkers.

## Constructor contract

A `TypedException` subclass constructor:

- accepts only keyword arguments for declared fields;
- rejects positional arguments;
- rejects unknown keyword arguments;
- requires every field that has no default and no factory;
- accepts an optional `message` argument that overrides the rendered template;
- validates field **presence**, not field value types.

Missing and unexpected names are reported in sorted order:

```python
class InvalidToken(TypedException):
    message_template = "Token {token_id} is invalid for {scope}"
    token_id: str
    scope: str


InvalidToken()
# TypeError: InvalidToken() missing required keyword-only argument(s):
# 'scope', 'token_id'

InvalidToken(token_id="a", scope="b", extra=1)
# TypeError: InvalidToken() got unexpected keyword argument(s): 'extra'
```

Field value types are checked only by a static type checker. This is valid at
runtime and should be caught by mypy or pyright instead:

```python
UserNotFound(user_id="42")  # runtime: accepted; type checker: error
```

## Defaults and factories

Use normal Python defaults for immutable values. Use a factory for mutable
values so each exception instance gets a fresh object.

```python
import dataclasses

from typed_exceptions.exception import TypedException, field


class ConfigWarning(TypedException):
    message_template = "Config {name} is using a deprecated option"
    name: str
    detail: str = "unknown"
    tags: list[str] = field(factory=list)
    cache: dict[str, str] = dataclasses.field(default_factory=dict)
```

The package provides its own `field()` helper:

```python
field(
    *,
    default=MISSING,
    factory=MISSING,
    redact=False,
    reserved=False,
)
```

- `default` and `factory` are mutually exclusive.
- `factory` must be a zero-argument callable.
- `redact=True` hides the value from `context` and forbids it in templates.
- `reserved=True` hides the value from `context` but keeps it accessible.
- The call returns an immutable `Field` specification that
  `__init_subclass__` collects.

`dataclasses.field` is also supported:

```python
class Example(TypedException):
    message_template = "value={value}"
    value: str = dataclasses.field(default="x")
    tags: list[str] = dataclasses.field(default_factory=list)
    secret: str = dataclasses.field(
        default="", metadata={"redact": True}
    )
    internal: int = dataclasses.field(
        default=0, metadata={"reserved": True}
    )
```

Supported `dataclasses.field` options:

- `default`
- `default_factory`
- `metadata={"redact": True}`
- `metadata={"reserved": True}`
- `kw_only=True` (or omitting `kw_only`)

Rejected `dataclasses.field` options:

- `init=False`
- `kw_only=False`
- a non-callable `default_factory`
- a non-boolean `metadata["redact"]` or `metadata["reserved"]`

Mutable class-level defaults are rejected because they would be shared between
instances:

```python
class Bad(TypedException):
    items: list[int] = []  # ValueError: unhashable default


class Good(TypedException):
    items: list[int] = field(factory=list)
```

## Message templates

`message_template` is compiled when the subclass is defined. Placeholders use a
simple `{field_name}` syntax:

```python
class InvalidToken(TypedException):
    message_template = "Token {token_id} is invalid for {scope}"
    token_id: str
    scope: str


print(str(InvalidToken(token_id="abc", scope="admin")))
# Token abc is invalid for admin
```

Placeholder values are rendered with `str()`, not `repr()`.

Only simple placeholders are supported. The following are rejected with
`ValueError` at class definition time:

- unknown field names: `{missing}`
- conversions: `{value!r}`
- format specifications: `{value:>5}`
- attribute access: `{value.real}`
- positional placeholders: `{}`

Literal braces are written as doubled braces:

```python
class Braces(TypedException):
    message_template = "{{literal}} {x}"
    x: int


print(str(Braces(x=1)))  # {literal} 1
```

A template with no placeholders is static and is cached on the class:

```python
class Static(TypedException):
    message_template = "always the same"
    value: int = 0


print(Static.__static_message__)  # always the same
```

Templates may reference any declared field, including reserved fields, but not
redacted fields:

```python
class Leaky(TypedException):
    message_template = "{secret}"
    secret: str = field(default="", redact=True)
# ValueError: redacted field 'secret' cannot appear in message_template
```

## Message overrides

Passing `message=` at construction time sets a literal message. The override is
not formatted and is not re-rendered from the template:

```python
class LayerFailure(TypedException):
    message_template = "Failure in {layer}"
    layer: str


error = LayerFailure(layer="cache", message="Cache {layer} unavailable")
print(str(error))  # Cache {layer} unavailable
```

Passing `MISSING` renders the template instead:

```python
from typed_exceptions.exception import MISSING

error = LayerFailure(layer="db", message=MISSING)
print(str(error))  # Failure in db
```

The override must be a string or `MISSING`:

```python
LayerFailure(layer="db", message=1)
# TypeError: LayerFailure(): message must be str | MISSING
```

After construction, assigning a string to `message` updates `args`, and
assigning a one-string tuple to `args` updates `message`:

```python
error = LayerFailure(layer="db")

error.message = "changed"
print(error.args)     # ('changed',)

error.args = ("other",)
print(error.message)  # other
```

Other assignments are rejected:

```python
error.message = None      # TypeError
error.args = ("a", "b")   # TypeError
del error.message         # AttributeError
```

## Structured context

The `context` property returns a new dictionary containing all non-reserved,
non-redacted fields in declaration order:

```python
class GrandChild(Child):
    message_template = "grand {a} {b} {c}"
    c: float = 1.5


error = GrandChild(a=1)
print(error.context)          # {'a': 1, 'b': 'b', 'c': 1.5}
print(list(error.context))    # ['a', 'b', 'c']
```

The values in `context` are the live field values, not copies. Mutable values
remain mutable:

```python
error = ConfigWarning(name="cfg")
error.tags.append("deprecated")
print(error.context["tags"])  # ['deprecated']
```

## Redaction

Mark sensitive fields in one of four ways:

```python
import dataclasses

from typed_exceptions.exception import TypedException, Redact, field


class SecretAccess(TypedException):
    message_template = "Access denied for {user}"
    user: str
    api_key: str = field(default="", redact=True)


class SecretAccessWithAnnotation(TypedException):
    message_template = "Access denied for {user}"
    user: str
    api_key: Redact[str] = ""


class SecretAccessWithMetadata(TypedException):
    message_template = "Access denied for {user}"
    user: str
    api_key: str = dataclasses.field(
        default="", metadata={"redact": True}
    )


class SecretAccessWithDunder(TypedException):
    message_template = "Access denied for {user}"
    __redact__ = frozenset({"api_key"})
    user: str
    api_key: str = ""
```

In every case:

```python
error = SecretAccess(user="alice", api_key="super-secret")
print(error.context)   # {'user': 'alice'}
print(error.api_key)   # super-secret
```

`Redact[T]` also works inside `Annotated[...]`, through nested PEP 695 type
aliases, and in stringized annotations, including modules that use
`from __future__ import annotations`.

Annotation markers must resolve by name from the class or module namespace.
With `from __future__ import annotations`, an alias imported in a local scope
(for example `from typed_exceptions.exception import Redact as R` inside a
function) cannot be recovered, and the marker is not detected. Use
`field(redact=True)` for security-sensitive fields; it does not depend on
annotation resolution.

Redaction rules:

- Redaction is inherited by subclasses.
- Redeclaring a redacted field does not remove its redaction.
- Redacted names cannot appear in `message_template`.
- `__redact__` must be a `set` or `frozenset` of strings.
- `__redact__` cannot name unknown fields.
- Redaction affects `context` only. It does **not** filter explicit `message`
  overrides, exception notes, nested values, or pickle data.

## Reserved fields

A reserved field stays accessible as an attribute but is excluded from
`context`. Reserved fields may still appear in message templates.

```python
class WithReserved(TypedException):
    message_template = "internal={internal} public={public}"
    internal: int = field(default=0, reserved=True)
    public: int = 0


error = WithReserved(internal=5, public=6)
print(str(error))       # internal=5 public=6
print(error.internal)   # 5
print(error.context)    # {'public': 6}
```

The same flag is available through dataclass metadata:

```python
internal: int = dataclasses.field(
    default=0, metadata={"reserved": True}
)
```

The following field names are reserved and cannot be declared:

- `args`
- `context`
- `with_traceback`
- `add_note`
- any name starting with `_`
- any non-identifier name

`message` is an implicit reserved field. Do not assign `message` in a class
body; use `message_template` instead:

```python
class Bad(TypedException):
    message = "nope"
# TypeError: Bad: use message_template = ... instead of message = ...
```

## Inheritance

Fields accumulate across the class hierarchy. Subclasses can add fields,
override defaults, make an inherited field required, or turn it into a
`ClassVar` to remove it.

```python
from typed_exceptions.exception import TypedException, field


class Base(TypedException):
    message_template = "base {a}"
    a: int
    secret: str = field(default="", redact=True)


class Child(Base):
    b: str = "b"


class GrandChild(Child):
    message_template = "grand {a} {b} {c}"
    c: float = 1.5


error = GrandChild(a=1)
print(str(error))       # grand 1 b 1.5
print(error.context)    # {'a': 1, 'b': 'b', 'c': 1.5}
print(error.secret)     # ""
```

- `Child` inherits the `Base` template.
- `GrandChild` replaces the template and adds `c`.
- The redacted `secret` field remains redacted in every subclass.
- `context` preserves declaration order across the hierarchy.

Making an inherited field required:

```python
class NowRequired(Child):
    b: str  # no default


NowRequired(a=1)
# TypeError: NowRequired() missing required keyword-only argument(s): 'b'
```

Removing an inherited field with `ClassVar`:

```python
from typing import ClassVar


class Hidden(Child):
    b: ClassVar[str] = "constant"


print(Hidden(a=1).context)  # {'a': 1}
```

## Runtime behavior

### Fields are mutable

Field values can be changed after construction:

```python
error = UserNotFound(user_id=1)
error.user_id = 2
print(error.context)  # {'user_id': 2}
```

### `message` and `args` stay in sync

`message` is always a plain `str`; `args` is always a one-element tuple
containing that string. `str(error)` returns the message.

Assigning a `str` subclass is normalized to a plain `str`, so a custom
`__str__` cannot disagree with the stored message text.

### Compiled class metadata is immutable

Metadata compiled at class definition time is frozen on the class that declares
it:

```python
UserNotFound.message_template = "changed"
# AttributeError: cannot modify frozen class attribute
# UserNotFound.message_template

del UserNotFound.__fields__
# AttributeError: cannot delete frozen class attribute
# UserNotFound.__fields__
```

The `__fields__` mapping is a read-only snapshot:

```python
fields = UserNotFound.__fields__
fields["x"] = fields["user_id"]  # TypeError
```

`Field` objects are immutable value objects. Assigning to one raises
`FrozenAttributeError`, a subclass of `AttributeError`.

Changing class declarations after the class is created is not supported. Fields
and templates are compiled when the subclass is defined.

## Introspection

Each `TypedException` subclass exposes compiled metadata:

| Attribute | Type | Description |
| --- | --- | --- |
| `__fields__` | read-only mapping | Field name to `Field`, including `message`, in declaration order |
| `__field_names__` | `frozenset[str]` | All field names, including `message` |
| `__required__` | `frozenset[Field]` | Fields without a default or factory |
| `__context_fields__` | `tuple[Field, ...]` | Fields included in `context`, in order |
| `__redact__` | `frozenset[str]` | Redacted field names |
| `__static_message__` | `str \| MISSING` | Cached message for templates without placeholders |
| `__dynamic_template__` | `DynamicTemplate` | Compiled template parts |

Example:

```python
print(list(UserNotFound.__fields__))          # ['message', 'user_id']
print(UserNotFound.__field_names__)           # frozenset({'message', 'user_id'})
print([f.name for f in UserNotFound.__required__])  # ['user_id']
print([f.name for f in UserNotFound.__context_fields__])  # ['user_id']
```

## Pickling, copying, and notes

`TypedException` supports `pickle`, `copy.copy`, and `copy.deepcopy`:

```python
import copy
import pickle

error = GrandChild(a=1, secret="s")
error.add_note("extra context")

restored = pickle.loads(pickle.dumps(error))
print(type(restored) is GrandChild)  # True
print(restored.context)              # {'a': 1, 'b': 'b', 'c': 1.5}
print(restored.__notes__)            # ['extra context']

clone = copy.deepcopy(error)
print(clone.context)                 # {'a': 1, 'b': 'b', 'c': 1.5}
```

Deep copy preserves reference cycles. The pickle implementation bypasses the
keyword-only constructor through `_restore_exception`; legacy pickle state in
the `args` tuple is still accepted.

## Type checking

`TypedException` is decorated with `dataclass_transform`:

```python
@dataclass_transform(
    kw_only_default=True,
    eq_default=False,
    field_specifiers=(field, dataclasses.field),
)
```

This gives type checkers the following view:

- declared fields become keyword-only constructor parameters;
- `message` is an optional string at construction;
- `message` is a string after construction;
- `field()` and `dataclasses.field()` declarations have the annotated type;
- no dataclass equality or ordering methods are generated.

Type checkers do not validate field values at runtime. Use mypy or pyright to
catch mismatched values:

```python
error = UserNotFound(user_id="42")  # static type error
```

The project itself is checked with mypy using strict-ish settings, including
`disallow_untyped_defs` and `disallow_any_generics`.

## API reference

### `TypedException`

Base class for all structured exceptions.

- Subclass of `Exception`.
- Keyword-only constructor for declared fields.
- Optional `message` argument for a literal override.
- `context` property returning non-reserved, non-redacted fields.
- Compiled metadata and frozen class attributes.
- Pickle, copy, deep copy, and note support.

### `field`

```python
field(
    *,
    default=MISSING,
    factory=MISSING,
    redact=False,
    reserved=False,
)
```

Declares a field on a `TypedException` subclass.

- `default`: default value; mutually exclusive with `factory`.
- `factory`: zero-argument callable producing a fresh default.
- `redact`: hide from `context` and forbid in templates.
- `reserved`: hide from `context` only.

Raises `ValueError` if both `default` and `factory` are provided.

### `Field`

Immutable specification of one exception field.

| Attribute | Description |
| --- | --- |
| `name` | Field name |
| `default` | Default value or `MISSING` |
| `factory` | Default factory or `MISSING` |
| `redact` | Whether the value is redacted |
| `reserved` | Whether the value is reserved |

### `MISSING`

A sentinel used to represent an omitted value or a field without a default.
Pass `message=MISSING` to render the template explicitly.

```python
from typed_exceptions.exception import MISSING
```

### `Redact`

`Redact[T]` is an `Annotated[T, ...]` alias that marks a field for redaction.
It is equivalent to `field(redact=True)` and is inherited by subclasses.

## Error reference

| Situation | Exception |
| --- | --- |
| Missing required field | `TypeError` |
| Unknown keyword argument | `TypeError` |
| Positional constructor argument | `TypeError` |
| Non-string `message` override | `TypeError` |
| Invalid `args` assignment | `TypeError` |
| Deleting `message` | `AttributeError` |
| Unhashable default | `ValueError` |
| Both `default` and `factory` | `ValueError` |
| Invalid template placeholder | `ValueError` |
| Redacted placeholder in template | `ValueError` |
| Invalid `__redact__` type or unknown name | `TypeError` |
| Reserved field name | `TypeError` |
| `message = ...` in a class body | `TypeError` |
| Non-string `message_template` | `TypeError` |
| Unsupported `dataclasses.field` option | `TypeError` |
| Frozen class metadata | `AttributeError` |
| Frozen `Field` attribute | `FrozenAttributeError` (subclass of `AttributeError`) |

## Design notes and limitations

- **No runtime type validation.** Field presence is checked; field value types
  are the responsibility of a static type checker.
- **Compilation happens at class definition.** Fields and templates are
  compiled in `__init_subclass__`. Changing declarations after class creation
  is not supported.
- **Keyword-only construction.** There is no positional constructor form.
- **Simple templates only.** Placeholders must be bare identifiers; no
  conversions, format specs, attribute access, or positional placeholders.
- **`message` is reserved.** Use `message_template` for templates and
  `message=` for literal overrides.
- **Redaction is not a security boundary.** It hides values from `context` and
  templates, but explicit message overrides, notes, nested values, and pickle
  data are not filtered.
- **Annotation markers must resolve.** `Redact[T]` in a stringized annotation
  works only when the name is visible in the class or module namespace. Local
  aliases under `from __future__ import annotations` are not detected; use
  `field(redact=True)` for security-sensitive fields.
- **Arbitrary instance attributes are allowed.** Assigning an unknown name
  creates a normal instance attribute; typos are not detected.
- **No dataclass semantics.** `eq`, ordering, and other dataclass-generated
  methods are not added.
- **Python 3.14+ only.** The code uses PEP 695 generics and PEP 649 annotation
  handling.
- **No CI or pre-commit configuration.** Run the checks manually.

## Development

Install all dependency groups:

```bash
uv sync --all-groups
```

Run the test suite:

```bash
uv run pytest -q
```

Run a single test:

```bash
uv run pytest tests/test_exception.py::test_quick_start -q
```

Type-check, lint, check import layers, and format-check:

```bash
uv run mypy src tests
uv run ruff check src tests
uv run ruff format --check src tests
uv run lint-imports
```

Run the tests with coverage:

```bash
uv run pytest --cov
```

Apply formatting:

```bash
uv run ruff format src tests
```

Build the package:

```bash
uv build
```

Notes:

- Plain `uv sync` installs only the `dev` group and uninstalls the `test`
  group. Use `--all-groups` to install pytest and coverage tooling.
- Tests live in a single file, `tests/test_exception.py`.
- The suite also runs the doctest in the `TypedException` docstring.
- Ruff enforces Google-style docstrings through the `D` rules with
  `convention = "google"`.
- mypy is configured with strict-ish settings; annotate every function,
  including tests.
- `lint-imports` enforces the package layer order configured in
  `pyproject.toml`.
- There is no CI or pre-commit configuration; run the commands above manually.

## Project layout

```text
typed_exceptions/
├── src/
│   └── typed_exceptions/
│       ├── __init__.py           # no exports; use the exception subpackage
│       └── exception/
│           ├── __init__.py       # public API: TypedException, field, Field,
│           │                     # MISSING, Redact
│           ├── exception.py      # runtime, metaclass, class construction
│           ├── field.py          # Field specifications, field(), dataclass interop
│           ├── template.py       # template compilation and rendering
│           ├── introspection.py  # PEP 649 annotation helpers
│           ├── access.py         # frozen value objects, Redact marker
│           └── sentinel.py       # MISSING sentinel
├── tests/
│   └── test_exception.py
├── LICENSE
├── pyproject.toml
├── README.md
├── README.ru.md
└── uv.lock
```

## License

The repository includes an MIT `LICENSE` file. Replace its `[fullname]`
placeholder before distributing the package.
