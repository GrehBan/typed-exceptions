# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`typed-exceptions` — a small, zero-magic-at-raise-time library: a
`TypedException` base class whose subclasses declare typed, keyword-only
fields, a `message_template` with `{field}` placeholders, and get a structured
`context` dict. Python **3.14+ only**. Single runtime dependency:
`typing-extensions` (for `Sentinel`). Formerly named `better_exceptions` —
do not reintroduce the old name.

## Commands

```bash
uv sync --all-groups                 # dev + test groups (plain `uv sync` skips test)
uv run pytest --cov                  # tests + branch coverage, fail_under = 90
uv run python -m doctest src/typed_exceptions/exception.py
uv run mypy src tests                # strict config in pyproject.toml
uv run ruff check .
uv run ruff format src tests         # `ruff format .` also rewrites code blocks in README*.md
uv run lint-imports                  # layer contract, see below
uv build                             # wheel + sdist into dist/
```

Run all of the above before calling a change done. Current baseline:
all green, coverage ~98%.

## Layout and layering

```
src/typed_exceptions/
  __init__.py              # public API: TypedException, Field, field, Redact, MISSING,
                           # snapshot_exception
  exception.py             # FrozenClassAttrsMeta, ExceptionRuntime, TypedException
  snapshot.py              # ExceptionChain/Snapshot, snapshot_exception
  template.py              # TemplateCompiler, DynamicTemplate(Part)
  field.py                 # Field spec, field(), dataclasses.field interop
  access.py                # Redact[T], should_redact
  introspection.py         # annotation reading / ClassVar detection
  sentinel.py              # MISSING
  py.typed
tests/test_exception.py    # all tests, one file
```

import-linter enforces this top-down order (a module may import only from
modules below it): `exception → snapshot → template → field → access →
introspection → sentinel`. The only allowed upward edge is `template → exception` under
`TYPE_CHECKING`. If you add a module, add it to
`[tool.importlinter.contracts]` in the right layer.

## How it works (read before changing core code)

- **Everything is compiled at class definition** in
  `TypedException.__init_subclass__`: fields are collected across the MRO,
  redaction resolved, `__required__` / `__initializers__` /
  `__context_fields__` precomputed, and the template parsed into
  `DynamicTemplate`. A template with no placeholders is pre-rendered into
  `__static_message__`. `__init__` only does lookups — keep it that way.
- **Compiled class attributes are frozen** by `FrozenClassAttrsMeta`. Any new
  compiled dunder must be added to `__frozen_attrs__`, to the
  `TYPE_CHECKING` block, and to the runtime defaults on `TypedException`.
- **Annotations are never evaluated.** They are read with
  `annotationlib.get_annotations(format=Format.FORWARDREF)` (PEP 649).
  String annotations are parsed with `ast` (`annotation_names`) and names are
  resolved only by reading `vars()` of the class and its module
  (`resolve_qualified_name`) — never `eval`, never trigger descriptors.
  `ClassVar` is detected at the top of the annotation only. `Redact[T]` is
  searched for **anywhere** in it (`access.meta_annotation_exists`): through
  `Annotated`, unions/`Optional`, generic arguments, PEP 695 aliases, strings,
  and PEP 649 `ForwardRef`s, whose already-resolved parts live in the
  undocumented `__extra_names__` (read defensively; a test pins the result).
  A marker anywhere hides the whole field; `Literal[...]` is skipped.
- **`message` is a reserved `Field`**, set last, after all other fields.
  `TypedException.__setattr__` keeps `message` and `args` in sync and
  normalises `str` subclasses via `str.__str__`. `message=` is a literal
  override, never formatted.
- **Pickle/copy** go through `__reduce__` → `_restore_exception`, which
  bypasses the keyword-only `__init__` (`cls(*args)` would fail).
  `__setstate__` handles both dict and `(dict, slots)` state.
- **`Field` and template parts are immutable** frozen slotted dataclasses
  (`@dataclasses.dataclass(frozen=True, slots=True)`), so copy/pickle work
  out of the box and they compare and hash by value. `Field` keeps a
  hand-written `__repr__` (`name` first) and its declaration order is the
  positional constructor order. `DynamicTemplate` takes a `parts` tuple and
  derives `names` in `__post_init__`. `ExceptionRuntime.__init__` takes
  `self` positional-only, so a field may be named `self`. `field_spec`
  always builds a fresh, name-bound `Field` copy.
- Field **value types are not validated at runtime** — only names, presence,
  and `message: str`. That is a design decision; don't add runtime type
  checks without discussing it.

## Contracts worth preserving

- Constructor is keyword-only; positional args → `TypeError`.
- Reserved field names: anything starting with `_`, non-identifiers, `args`,
  `context`, `with_traceback`, `add_note`. Declaring `message` → `TypeError`.
- Definition-time problems: `TypeError` for structure (bad `__redact__`,
  reserved names, bad dataclass field), `ValueError` for template placeholders
  and unhashable defaults. Error messages are prefixed with the class name.
- Redaction is inherited and cannot be undone by redeclaring a field; redacted
  fields are excluded from `context` and forbidden in templates.
- A placeholder whose value's `__str__` raises renders as
  `<unprintable module.TypeName>`; never let that error escape `__init__`.
- `message_template: str = ...` (annotated, no ClassVar) is still the
  template, not a field.
- `Cls(..., message_template="...")` renders a per-instance template through
  the same `TemplateCompiler` (cached in `_instance_template`); it conflicts
  with a literal `message=` (`TypeError`) and never becomes a field. The
  `TYPE_CHECKING` block declares `message_template: str = ""` (not
  `ClassVar`, and not `str | MISSING`) so mypy accepts the keyword without
  breaking subclass overrides; keep it that way. The price is documented:
  mypy rejects `ClassVar[str]`/`Final` template annotations and
  `message=MISSING`; tests pin both with `# type: ignore[...]` comments
  (`warn_unused_ignores` fails the build if that ever changes).
- Fields are intentionally mutable (documented, tested, and a subclass
  `__setattr__` is honoured during init). A message rendered from a template
  is "live": assigning a placeholder field re-renders `message`/`args`
  (`_refresh_message`). State is one instance attribute, `__live_message__`
  (`True` = class template, `str` = per-instance template, absent =
  literal or placeholder-free), kept as plain data so pickle/copy restore it
  (the frozen dataclasses `Field` and `DynamicTemplate` pickle fine).
  A literal `message=` or an explicit `message`/`args` assignment drops the
  attribute; in-place mutation of a value is not detected.
- `message`/`message_template` are guarded by the metaclass on every
  subclass (`_is_frozen_attr`), because assigning them on an inheriting
  subclass would be a silent no-op. The metaclass also means `ABC`/`Protocol`
  mixins raise a metaclass conflict; that is documented, not a bug.
- Snapshots (`snapshot.py`) are immutable dataclasses. `context` is reduced
  to JSON types at capture (`_plain`: containers copied, unknown objects
  `str()`-ed, depth capped), so a snapshot never holds live objects and
  `to_json` cannot raise. Chains are walked breadth-first with each exception
  numbered once; `limit` cuts the walk and sets `truncated`. With
  `capture_locals`, locals named like any redacted field in the chain are
  hidden and long reprs are clipped; this is a name heuristic, documented as
  such. `redacted` is sorted and `()` stays distinct from `None` (not typed).
- Templates accept only `{identifier}` — no format specs, conversions,
  attribute/index access, or positional placeholders.
- `dataclass_transform(kw_only_default=True, eq_default=False,
  field_specifiers=(field, dataclasses.field))` on `ExceptionRuntime` is what
  makes type checkers understand subclasses; keep `field()` overloads in sync
  with it.

## Code style

- Line length 79, double quotes, ruff rules `E F C4 C90 Q RET I UP T10 D`,
  max McCabe complexity 10.
- Google-style docstrings on every public module, class and function (ruff
  `D`). Private helpers may skip them.
- mypy strict (`disallow_untyped_defs`, `disallow_any_generics`, ...). Use
  PEP 695 syntax (`def f[T](...)`, `type X = ...`); `UP040`/`UP046` are
  ignored on purpose.
- Python 3.14 syntax is fine and already used (e.g. `except A, B:` from
  PEP 758).
- Comments explain *why* (see the pickle and PEP 649 comments); keep that
  tone.

## Tests

- All tests live in `tests/test_exception.py`; pytest runs with
  `--import-mode=importlib` and `pythonpath = ["src"]`.
- Every behaviour change needs a test, including the error message text for
  new `TypeError`/`ValueError` paths. Keep branch coverage ≥ 90 (aim to keep
  the current ~98%).
- The `TypedException` docstring contains a doctest — keep it passing.

## Docs

- `README.md` (EN) and `README.ru.md` (RU) are full user guides with API and
  error references. Any public behaviour change must update **both**.
- README code blocks are formatted by `ruff format`, so format them after
  editing.

## Release

- Version lives only in `pyproject.toml` (`[project].version`); commits are
  named after the version (`0.2.0`).
- `dist/` is git-ignored and may contain stale `better_exceptions-*` builds:
  clear it before `uv build` / publishing.
