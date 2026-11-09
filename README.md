# better-exceptions

`better-exceptions` is a small Python library for defining exceptions with typed, structured context and readable formatted messages.

It gives you a `BetterException` base class that behaves like a regular Python exception, while enforcing a clearer error contract for your application code.

## Why use it?

Typical exceptions are either plain strings or dictionaries attached after the fact. `BetterException` makes error data part of the exception itself:

- typed context fields on the exception class
- keyword-only constructor arguments
- required fields enforced at runtime
- message templates with `{field_name}` placeholders
- redaction support for sensitive values
- a `context` property that exposes non-redacted fields

## Requirements

- Python 3.14+

## Installation

From the project root:

```bash
pip install .
```

Or with `uv`:

```bash
uv sync
```

## Quick start

```python
from better_exceptions.exception import BetterException


class UserNotFound(BetterException):
    message_template = "User {user_id} was not found"
    user_id: int


error = UserNotFound(user_id=42)

print(str(error))
# User 42 was not found

print(error.context)
# {'user_id': 42}
```

## Defining exceptions

Subclass `BetterException` and declare your fields as annotated attributes. Use `message_template` to build the rendered string automatically.

```python
from better_exceptions.exception import BetterException


class InvalidToken(BetterException):
    message_template = "Token {token_id} is invalid for {scope}"
    token_id: str
    scope: str
```

The constructor only accepts keyword arguments for declared fields, and required fields are validated at runtime.

```python
InvalidToken(token_id="abc", scope="admin")
# OK

InvalidToken(scope="admin")
# TypeError: InvalidToken() missing required keyword-only argument(s): 'token_id'
```

## Optional/default values

Use normal Python defaults or `field(default_factory=...)` when a field should be optional or lazily initialized.

```python
from dataclasses import field

from better_exceptions.exception import BetterException


class ConfigWarning(BetterException):
    message_template = "Config {name} is using a deprecated option"
    name: str
    detail: str = "unknown"
    tags: list[str] = field(default_factory=list)
```

## Redacting sensitive fields

Mark a field with `metadata={"redact": True}` to keep it out of the public `context` mapping and prevent it from appearing in `message_template`.

```python
from dataclasses import field

from better_exceptions.exception import BetterException


class SecretAccess(BetterException):
    message_template = "Access denied for {user}"
    user: str
    api_key: str = field(default="", metadata={"redact": True})
```

```python
error = SecretAccess(user="alice", api_key="super-secret")
print(error.context)
# {'user': 'alice'}
```

## Message override

If you need a literal message rather than a template, set `message` at construction time:

```python
class LayerFailure(BetterException):
    message_template = "Failure in {layer}"
    layer: str

error = LayerFailure(layer="cache", message="Cache backend unavailable")
print(str(error))
# Cache backend unavailable
```

`message` is treated as a final override for the rendered template; it is not re-formatted.

## Project layout

```text
better_exceptions/
├── src/
│   └── better_exceptions/
│       ├── exception.py
│       ├── helper/
│       └── validator/
├── pyproject.toml
├── README.md
└── uv.lock
```

## Development

```bash
uv sync
python -m pytest
```

The project is configured for Python 3.14 features and uses `ruff`/`mypy` in the tool configuration.

## License

This project is distributed under the repository's license terms.
