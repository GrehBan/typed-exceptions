# typed-exceptions

[English](README.md) | Русский

Типизированные структурированные исключения с шаблонами сообщений для
Python 3.14+.

`typed-exceptions` предоставляет базовый класс `TypedException` (импортируется
из `typed_exceptions`), который ведёт себя как обычное исключение Python, но
делает данные об ошибке частью самого исключения: типизированные поля,
конструирование только по именам, структурированный контекст и читаемые шаблоны
сообщений.

## Содержание

- [Зачем нужен typed-exceptions?](#зачем-нужен-typed-exceptions)
- [Возможности](#возможности)
- [Требования](#требования)
- [Установка](#установка)
- [Быстрый старт](#быстрый-старт)
- [Объявление исключений](#объявление-исключений)
- [Контракт конструктора](#контракт-конструктора)
- [Значения по умолчанию и фабрики](#значения-по-умолчанию-и-фабрики)
- [Шаблоны сообщений](#шаблоны-сообщений)
- [Переопределение сообщения](#переопределение-сообщения)
- [Структурированный контекст](#структурированный-контекст)
- [Скрытие конфиденциальных полей](#скрытие-конфиденциальных-полей)
- [Зарезервированные поля](#зарезервированные-поля)
- [Наследование](#наследование)
- [Поведение во время выполнения](#поведение-во-время-выполнения)
- [Интроспекция](#интроспекция)
- [Сериализация, копирование и заметки](#сериализация-копирование-и-заметки)
- [Снимки исключений](#снимки-исключений)
- [Проверка типов](#проверка-типов)
- [Справочник по API](#справочник-по-api)
- [Справочник ошибок](#справочник-ошибок)
- [Замечания по дизайну и ограничения](#замечания-по-дизайну-и-ограничения)
- [Разработка](#разработка)
- [Структура проекта](#структура-проекта)
- [Лицензия](#лицензия)

## Зачем нужен typed-exceptions?

Обычные исключения обычно несут либо отформатированную строку, либо словарь,
прикреплённый уже после создания. Из-за этого данные об ошибке трудно
обнаружить, легко опечататься, и неудобно обрабатывать программно.

`TypedException` делает данные частью класса исключения:

- Поля объявляются обычными аннотациями типов.
- Конструирование выполняется только по именам и проверяет обязательные поля во
  время выполнения.
- Сообщение можно отрисовать из шаблона или переопределить буквально.
- Нечувствительные поля доступны как структурированное отображение `context`.
- Чувствительные поля можно скрыть.
- Статические анализаторы типов понимают конструктор и типы полей.

## Возможности

- Типизированные аннотированные поля контекста в подклассах исключений
- Конструкторы только по именам с проверкой наличия полей во время выполнения
- Обязательные поля, значения по умолчанию и фабрики по умолчанию
- Совместимость с `dataclasses.field`
- Шаблоны сообщений с простыми заполнителями `{field_name}`
- Буквальное переопределение сообщения и шаблон, заданный для экземпляра
- Сообщение, которое следует за используемыми полями при их переприсваивании
- Структурированное отображение `context` в порядке объявления
- Скрытие через `field(redact=True)`, метаданные dataclass, `Redact[T]` или
  `__redact__`
- Зарезервированные поля, которые остаются доступными, но скрыты из `context`
- Наследование, переопределение полей и унаследованные шаблоны
- Неизменяемые скомпилированные метаданные класса и доступные только для чтения
  спецификации полей
- Pickle, поверхностное и глубокое копирование, а также заметки исключений
- Неизменяемые снимки цепочек исключений в формате JSON с учётом скрытых
  полей в локальных переменных
- Поддержка статических анализаторов типов через `dataclass_transform`
- Отсутствие проверки типов значений полей во время выполнения

## Требования

- Python 3.14 или новее
- [`typing-extensions`](https://pypi.org/project/typing-extensions/)
  (`>=4.16.0`, устанавливается автоматически)

Реализация использует возможности Python 3.14, такие как обобщения и псевдонимы
типов PEP 695, аннотации PEP 649 и `typing_extensions.Sentinel`. Более старые
версии Python не поддерживаются.

## Установка

С помощью `uv`:

```bash
uv sync --all-groups
```

Или с помощью `pip` из корня проекта:

```bash
pip install .
```

`uv sync` без `--all-groups` устанавливает только группу зависимостей `dev`;
группа `test` отдельная.

## Быстрый старт

```python
from typed_exceptions import TypedException


class UserNotFound(TypedException):
    message_template = "User {user_id} was not found"
    user_id: int


error = UserNotFound(user_id=42)

print(str(error))  # User 42 was not found
print(error.message)  # User 42 was not found
print(error.args)  # ('User 42 was not found',)
print(error.context)  # {'user_id': 42}
print(error.user_id)  # 42
```

`TypedException` — обычный подкласс `Exception`, поэтому его можно возбуждать
и перехватывать как обычно:

```python
try:
    raise UserNotFound(user_id=42)
except UserNotFound as error:
    print(error.context["user_id"])  # 42
```

## Объявление исключений

Унаследуйтесь от `TypedException` и объявите поля как аннотированные атрибуты.
Используйте `message_template`, чтобы задать способ отрисовки сообщения.

```python
from typed_exceptions import TypedException


class InvalidToken(TypedException):
    message_template = "Token {token_id} is invalid for {scope}"
    token_id: str
    scope: str
```

Поля без значения по умолчанию обязательны. Поля со значением по умолчанию или
с фабрикой необязательны.

```python
InvalidToken(token_id="abc", scope="admin")  # OK
InvalidToken(scope="admin")  # TypeError: missing 'token_id'
```

Конструктор принимает только именованные аргументы. Позиционные аргументы
отклоняются, а неизвестные именованные аргументы вызывают `TypeError` с
отсортированным списком имён.

```python
InvalidToken("abc", "admin")  # TypeError
InvalidToken(token_id="a", scope="b", extra=1)  # TypeError
```

Аннотации `ClassVar` не являются полями:

```python
from typing import ClassVar

from typed_exceptions import TypedException


class WithClassVar(TypedException):
    code: ClassVar[int] = 404
    detail: str = "x"


print(WithClassVar().context)  # {'detail': 'x'}
```

Используйте `ClassVar[T]`, чтобы исключить аннотированный атрибут из схемы
полей, оставив атрибут доступным:

```python
from typing import ClassVar

from typed_exceptions import TypedException


class WithConstant(TypedException):
    internal: ClassVar[int] = 0
    visible: int = 1


error = WithConstant()
print(error.internal)  # 0
print(error.context)  # {'visible': 1}
```

`ClassVar[T]` также может удалить поле, унаследованное от базового класса. Его
понимают и рантайм, и статические анализаторы типов.

## Контракт конструктора

Конструктор подкласса `TypedException`:

- принимает только именованные аргументы для объявленных полей;
- отклоняет позиционные аргументы;
- отклоняет неизвестные именованные аргументы;
- требует каждое поле, у которого нет ни значения по умолчанию, ни фабрики;
- принимает необязательный аргумент `message`, переопределяющий отрисованный
  шаблон;
- проверяет **наличие** полей, а не типы их значений.

Отсутствующие и неожиданные имена сообщаются в отсортированном порядке:

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

Типы значений полей проверяет только статический анализатор типов. Следующий код
корректен во время выполнения, а ошибку должен поймать mypy или pyright:

```python
UserNotFound(user_id="42")  # runtime: accepted; type checker: error
```

## Значения по умолчанию и фабрики

Используйте обычные значения Python по умолчанию для неизменяемых значений.
Для изменяемых значений используйте фабрику, чтобы каждый экземпляр исключения
получал свежий объект.

```python
import dataclasses

from typed_exceptions import TypedException, field


class ConfigWarning(TypedException):
    message_template = "Config {name} is using a deprecated option"
    name: str
    detail: str = "unknown"
    tags: list[str] = field(factory=list)
    cache: dict[str, str] = dataclasses.field(default_factory=dict)
```

Пакет предоставляет собственный помощник `field()`:

```python
field(
    *,
    default=MISSING,
    factory=MISSING,
    redact=False,
    reserved=False,
)
```

- `default` и `factory` взаимоисключающие.
- `factory` должен быть вызываемым объектом без аргументов.
- `redact=True` скрывает значение из `context` и запрещает его в шаблонах.
- `reserved=True` скрывает значение из `context`, но оставляет его доступным.
- Вызов возвращает неизменяемую спецификацию `Field`, которую собирает
  `__init_subclass__`.

`dataclasses.field` также поддерживается:

```python
class Example(TypedException):
    message_template = "value={value}"
    value: str = dataclasses.field(default="x")
    tags: list[str] = dataclasses.field(default_factory=list)
    secret: str = dataclasses.field(default="", metadata={"redact": True})
    internal: int = dataclasses.field(default=0, metadata={"reserved": True})
```

Поддерживаемые параметры `dataclasses.field`:

- `default`
- `default_factory`
- `metadata={"redact": True}`
- `metadata={"reserved": True}`
- `kw_only=True` (или отсутствие `kw_only`)

Отклоняемые параметры `dataclasses.field`:

- `init=False`
- `kw_only=False`
- `default_factory`, не являющийся вызываемым объектом
- небулево значение `metadata["redact"]` или `metadata["reserved"]`

Изменяемые значения по умолчанию на уровне класса отклоняются, поскольку они
разделялись бы между экземплярами:

```python
class Bad(TypedException):
    items: list[int] = []  # ValueError: unhashable default


class Good(TypedException):
    items: list[int] = field(factory=list)
```

## Шаблоны сообщений

`message_template` компилируется при определении подкласса. Заполнители
используют простой синтаксис `{field_name}`:

```python
class InvalidToken(TypedException):
    message_template = "Token {token_id} is invalid for {scope}"
    token_id: str
    scope: str


print(str(InvalidToken(token_id="abc", scope="admin")))
# Token abc is invalid for admin
```

Значения заполнителей отрисовываются через `str()`, а не через `repr()`. Если `__str__` значения
выбрасывает исключение, заполнитель выводится как
`<unprintable module.TypeName>`, чтобы исходная ошибка не подменялась чужой.

Шаблон объявляется обычным атрибутом класса. Даже с аннотацией
(`message_template: str = "..."`) он остаётся шаблоном и не становится полем.

Поддерживаются только простые заполнители. Следующие варианты отклоняются с
`ValueError` во время определения класса:

- неизвестные имена полей: `{missing}`
- преобразования: `{value!r}`
- спецификации формата: `{value:>5}`
- доступ к атрибутам: `{value.real}`
- позиционные заполнители: `{}`

Литеральные фигурные скобки записываются удвоенными:

```python
class Braces(TypedException):
    message_template = "{{literal}} {x}"
    x: int


print(str(Braces(x=1)))  # {literal} 1
```

Шаблон без заполнителей статический и кэшируется в классе:

```python
class Static(TypedException):
    message_template = "always the same"
    value: int = 0


print(Static.__static_message__)  # always the same
```

Шаблоны могут ссылаться на любое объявленное поле, включая зарезервированные,
но не на скрытые поля:

```python
class Leaky(TypedException):
    message_template = "{secret}"
    secret: str = field(default="", redact=True)


# ValueError: redacted field 'secret' cannot appear in message_template
```

## Переопределение сообщения

Передача `message=` при конструировании задаёт буквальное сообщение.
Переопределение не форматируется и не отрисовывается заново из шаблона:

```python
class LayerFailure(TypedException):
    message_template = "Failure in {layer}"
    layer: str


error = LayerFailure(layer="cache", message="Cache {layer} unavailable")
print(str(error))  # Cache {layer} unavailable
```

Передача `MISSING` вместо этого отрисовывает шаблон:

```python
from typed_exceptions import MISSING

error = LayerFailure(layer="db", message=MISSING)
print(str(error))  # Failure in db
```

Переопределение должно быть строкой или `MISSING`:

```python
LayerFailure(layer="db", message=1)
# TypeError: LayerFailure(): message must be str | MISSING
```

Чтобы отрисовать другой *шаблон* для одного экземпляра, передайте
`message_template=`. В отличие от буквального `message=`, он форматируется
полями экземпляра:

```python
error = LayerFailure(
    layer="cache", message_template="Cache {layer} unavailable"
)
print(str(error))  # Cache cache unavailable
print(str(LayerFailure(layer="db")))  # Failure in db
```

Шаблон экземпляра подчиняется тем же правилам, что и шаблон класса: допустимы
только простые заполнители `{field_name}`, `{{` и `}}` дают литеральные
скобки, а неизвестный, неподдерживаемый или скрытый заполнитель вызывает
`ValueError` (с префиксом `LayerFailure.message_template`). Замечания:

- `message=` и `message_template=` нельзя сочетать (`TypeError`);
  `MISSING` в обоих случаях означает «не передано».
- Атрибут класса не меняется, а `message_template` никогда не становится полем
  и не попадает в `context`.
- Как и любое шаблонное сообщение, оно перерисовывается при присваивании
  используемого в нём поля.
- Скомпилированные шаблоны экземпляров кэшируются по классу и строке шаблона.
- Для type checker'ов `message_template` — необязательный аргумент `str`.

После конструирования присваивание строки в `message` обновляет `args`, а
присваивание кортежа из одной строки в `args` обновляет `message`:

```python
error = LayerFailure(layer="db")

error.message = "changed"
print(error.args)  # ('changed',)

error.args = ("other",)
print(error.message)  # other
```

Другие присваивания отклоняются:

```python
error.message = None  # TypeError
error.args = ("a", "b")  # TypeError
del error.message  # AttributeError
```

## Структурированный контекст

Свойство `context` возвращает новый словарь со всеми незарезервированными и
нескрытыми полями в порядке объявления:

```python
class GrandChild(Child):
    message_template = "grand {a} {b} {c}"
    c: float = 1.5


error = GrandChild(a=1)
print(error.context)  # {'a': 1, 'b': 'b', 'c': 1.5}
print(list(error.context))  # ['a', 'b', 'c']
```

Значения в `context` — это актуальные значения полей, а не копии. Изменяемые
значения остаются изменяемыми:

```python
error = ConfigWarning(name="cfg")
error.tags.append("deprecated")
print(error.context["tags"])  # ['deprecated']
```

## Скрытие конфиденциальных полей

Отметьте чувствительные поля одним из четырёх способов:

```python
import dataclasses

from typed_exceptions import TypedException, Redact, field


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
    api_key: str = dataclasses.field(default="", metadata={"redact": True})


class SecretAccessWithDunder(TypedException):
    message_template = "Access denied for {user}"
    __redact__ = frozenset({"api_key"})
    user: str
    api_key: str = ""
```

Во всех случаях:

```python
error = SecretAccess(user="alice", api_key="super-secret")
print(error.context)  # {'user': 'alice'}
print(error.api_key)  # super-secret
```

`Redact[T]` обнаруживается в любом месте аннотации, поэтому он работает и:

- внутри `Annotated[...]` и вложенных псевдонимов типов PEP 695;
- в объединениях и необязательных типах: `Redact[str] | None`,
  `Optional[Redact[str]]`;
- в аргументах обобщённых типов: `list[Redact[str]]`,
  `dict[str, Redact[str]]`;
- в строковых аннотациях, включая модули с `from __future__ import
  annotations`, и когда часть аннотации называет то, что определено в модуле
  позже.

Маркер в любом месте скрывает **всё** поле целиком, потому что поле нельзя
скрыть частично. Аргументы `Literal[...]` никогда не считаются маркерами.

Имена маркеров в аннотациях должны разрешаться из пространства имён класса или
модуля. При `from __future__ import annotations` псевдоним, импортированный в
локальной области (например,
`from typed_exceptions import Redact as R` внутри функции), не может
быть восстановлен, и маркер не обнаруживается. Для конфиденциальных полей
используйте `field(redact=True)`: он не зависит от разрешения аннотаций.

Правила скрытия:

- Скрытие наследуется подклассами.
- Повторное объявление скрытого поля не снимает скрытие.
- Скрытые имена не могут появляться в `message_template`.
- `__redact__` должен быть `set` или `frozenset` строк.
- `__redact__` не может называть неизвестные поля.
- `__redact__` не может содержать `"message"`.
- Скрытие влияет только на `context`. Оно **не** фильтрует явные
  переопределения `message`, заметки исключений, вложенные значения или данные
  pickle.

## Зарезервированные поля

Зарезервированное поле остаётся доступным как атрибут, но исключается из
`context`. Зарезервированные поля по-прежнему могут появляться в шаблонах
сообщений.

```python
class WithReserved(TypedException):
    message_template = "internal={internal} public={public}"
    internal: int = field(default=0, reserved=True)
    public: int = 0


error = WithReserved(internal=5, public=6)
print(str(error))  # internal=5 public=6
print(error.internal)  # 5
print(error.context)  # {'public': 6}
```

Тот же флаг доступен через метаданные dataclass:

```python
internal: int = dataclasses.field(default=0, metadata={"reserved": True})
```

Следующие имена полей зарезервированы и не могут быть объявлены:

- `args`
- `context`
- `with_traceback`
- `add_note`
- любое имя, начинающееся с `_`
- любое имя, не являющееся идентификатором

`message` — неявное зарезервированное поле. Не присваивайте `message` в теле
класса; вместо этого используйте `message_template`:

```python
class Bad(TypedException):
    message = "nope"


# TypeError: Bad: use message_template = ... instead of message = ...
```

## Наследование

Поля накапливаются по всей иерархии классов. Подклассы могут добавлять поля,
переопределять значения по умолчанию, делать унаследованное поле обязательным
или превращать его в `ClassVar`, чтобы удалить.

```python
from typed_exceptions import TypedException, field


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
print(str(error))  # grand 1 b 1.5
print(error.context)  # {'a': 1, 'b': 'b', 'c': 1.5}
print(error.secret)  # ""
```

- `Child` наследует шаблон `Base`.
- `GrandChild` заменяет шаблон и добавляет `c`.
- Скрытое поле `secret` остаётся скрытым во всех подклассах.
- `context` сохраняет порядок объявления по всей иерархии.

Сделать унаследованное поле обязательным:

```python
class NowRequired(Child):
    b: str  # no default


NowRequired(a=1)
# TypeError: NowRequired() missing required keyword-only argument(s): 'b'
```

Удаление унаследованного поля с помощью `ClassVar`:

```python
from typing import ClassVar


class Hidden(Child):
    b: ClassVar[str] = "constant"


print(Hidden(a=1).context)  # {'a': 1}
```

## Поведение во время выполнения

### Поля изменяемы

Значения полей можно менять после конструирования:

```python
error = UserNotFound(user_id=1)
error.user_id = 2
print(error.context)  # {'user_id': 2}
```

Сообщение, отрисованное из шаблона, следует за используемыми полями.
Присваивание поля, которое входит в шаблон, перерисовывает сообщение и
сохраняет `message` и `args` синхронизированными:

```python
print(str(error))  # User 2 was not found
print(error.args)  # ('User 2 was not found',)
```

Сообщение **не** перерисовывается, если:

- `message=` был передан в конструктор (это буквальное сообщение);
- `message` или `args` присваивали явно (с этого момента сообщение буквальное);
- значение изменили на месте, например `error.tags.append("x")`, потому что
  это невозможно обнаружить;
- присвоенное поле не входит в шаблон.

Шаблон экземпляра, переданный как `message_template=`, перерисовывается так же.
Исключения, чей шаблон не содержит заполнителей, не несут лишнего состояния.

### `message` и `args` синхронизированы

`message` всегда является обычной строкой `str`; `args` всегда является
кортежем из одного элемента, содержащим эту строку. `str(error)` возвращает
сообщение.

Присваивание подкласса `str` нормализуется до обычной `str`, поэтому
пользовательский `__str__` не может расходиться с сохранённым текстом
сообщения.

Присваивание `message` (или `args`) превращает сообщение в буквальное: дальнейшие
присваивания полей его больше не меняют.

### Скомпилированные метаданные класса неизменяемы

Метаданные, скомпилированные при определении класса, замораживаются.
`message_template` защищён в каждом подклассе, в том числе в тех, что только
наследуют его, потому что поздняя замена молча не повлияла бы на уже
скомпилированное сообщение:

```python
UserNotFound.message_template = "changed"
# AttributeError: cannot modify frozen class attribute
# UserNotFound.message_template

del UserNotFound.__fields__
# AttributeError: cannot delete frozen class attribute
# UserNotFound.__fields__
```

Отображение `__fields__` — доступный только для чтения снимок:

```python
fields = UserNotFound.__fields__
fields["x"] = fields["user_id"]  # TypeError
```

Объекты `Field` — неизменяемые value-объекты. Присваивание им вызывает
`dataclasses.FrozenInstanceError`, подкласс `AttributeError`.

Защита класса реализована метаклассом. Сочетание `TypedException` с другим
метаклассом (`abc.ABC`, `typing.Protocol` и т. д.) вызывает `TypeError:
metaclass conflict`. `ABC` всё равно не заставит соблюдать абстрактные методы у
исключения, поэтому «не создавать эту базу напрямую» лучше выражать
соглашением. Если второй метакласс действительно нужен, объедините их сами:

```python
from abc import ABCMeta

from typed_exceptions.exception import FrozenClassAttrsMeta


class Meta(FrozenClassAttrsMeta, ABCMeta):
    pass


class Base(TypedException, metaclass=Meta):
    message_template = "base"
```

Изменение объявлений класса после его создания не поддерживается. Поля и
шаблоны компилируются при определении подкласса.

## Интроспекция

Каждый подкласс `TypedException` предоставляет скомпилированные метаданные:

| Атрибут | Тип | Описание |
| --- | --- | --- |
| `__fields__` | отображение только для чтения | Имя поля → `Field`, включая `message`, в порядке объявления |
| `__field_names__` | `frozenset[str]` | Все имена полей, включая `message` |
| `__required__` | `frozenset[Field]` | Поля без значения по умолчанию или фабрики |
| `__context_fields__` | `tuple[Field, ...]` | Поля, включаемые в `context`, по порядку |
| `__redact__` | `frozenset[str]` | Имена скрытых полей |
| `__static_message__` | `str \| MISSING` | Кэшированное сообщение для шаблонов без заполнителей |
| `__dynamic_template__` | `DynamicTemplate` | Скомпилированные части шаблона |

Пример:

```python
print(list(UserNotFound.__fields__))  # ['message', 'user_id']
print(UserNotFound.__field_names__)  # frozenset({'message', 'user_id'})
print([f.name for f in UserNotFound.__required__])  # ['user_id']
print([f.name for f in UserNotFound.__context_fields__])  # ['user_id']
```

## Сериализация, копирование и заметки

`TypedException` поддерживает `pickle`, `copy.copy` и `copy.deepcopy`:

```python
import copy
import pickle

error = GrandChild(a=1, secret="s")
error.add_note("extra context")

restored = pickle.loads(pickle.dumps(error))
print(type(restored) is GrandChild)  # True
print(restored.context)  # {'a': 1, 'b': 'b', 'c': 1.5}
print(restored.__notes__)  # ['extra context']

clone = copy.deepcopy(error)
print(clone.context)  # {'a': 1, 'b': 'b', 'c': 1.5}
```

Глубокое копирование сохраняет циклические ссылки. Реализация pickle обходит
конструктор только по именам через `_restore_exception`; устаревшее состояние
pickle в кортеже `args` по-прежнему принимается.

## Снимки исключений

`TypedException.snapshot()` и `snapshot_exception()` фиксируют исключение и
всё, что от него достижимо (`__cause__`, `__context__` и члены группы
исключений), в виде неизменяемых простых данных. Снимок можно залогировать,
сохранить или передать по сети, когда исходных объектов уже нет.

```python
from typed_exceptions import Redact, TypedException, snapshot_exception


class DbError(TypedException):
    message_template = "query failed on {table}"
    table: str
    password: Redact[str] = ""


def run_query(password: str) -> None:
    try:
        raise ConnectionError("refused")
    except ConnectionError as exc:
        raise DbError(table="users", password=password) from exc


try:
    run_query("hunter2")
except DbError as error:
    chain = error.snapshot(schema_version="1", capture_locals=True)
    short = snapshot_exception(error, schema_version="1", limit=1)

top, cause = chain.items
print(top.type, top.message)  # __main__.DbError query failed on users
print(top.context, top.redacted)  # {'table': 'users'} ('password',)
print(top.cause_id, cause.type)  # 1 builtins.ConnectionError
print(cause.context)  # None
print(top.trace[-1].locals)  # {'password': '<redacted>'}
print(chain.truncated, short.truncated)  # False True
print(len(short.items), short.items[0].cause_id)  # 1 None
```

`snapshot_exception(exc, *, schema_version, capture_locals=False, limit=253)`
работает с любым исключением. У экземпляров `TypedException` есть также метод
`snapshot()`, где `schema_version` по умолчанию равен `"1"`. Обе функции
возвращают `ExceptionChain` (импортируется из `typed_exceptions.snapshot`) с
полями `items`, `schema_version`, `truncated` и методами `to_dict()` и
`to_json()`.

Каждый элемент — `ExceptionSnapshot`:

| Поле | Описание |
| --- | --- |
| `id` | Позиция в `items`; снятое исключение имеет `0` |
| `type` | Полное имя класса, например `builtins.KeyError` |
| `message` | `str()` исключения или заглушка, если он упал |
| `context` | `context` у `TypedException`, `None` у остальных исключений |
| `redacted` | Отсортированные имена скрытых полей, `None` у остальных |
| `context_id`, `cause_id` | `id` для `__context__` / `__cause__` или `None` |
| `suppress_context` | Значение `__suppress_context__` |
| `group_ids` | `id` каждого снятого члена группы исключений |
| `trace` | Кадры как `FrameSnapshot` (`file_name`, `line_number`, `name`, `line_text`, `locals`) |
| `notes` | Заметки исключения в виде строк |

Исключения ссылаются друг на друга по `id`, поэтому общая или циклическая
цепочка остаётся конечной. Элементы идут в порядке обхода в ширину, причины
раньше контекстов.

`to_dict()` возвращает `{"root": 0, "exceptions": [...], "schema_version": ...,
"truncated": ...}`. `schema_version` — лишь метка, которую выбираете вы;
библиотека её не интерпретирует. `to_json()` не падает на необычных
значениях: всё, что JSON не умеет кодировать, записывается как `str()`.

### Что сохраняет снимок

- **`context` отвязан от живых объектов.** Значения приводятся к типам JSON в
  момент снимка: кортежи становятся списками, множества — отсортированными
  списками, ключи словарей — строками, `NaN` и бесконечность — строками, а
  любой другой объект — своим `str()`. Вложенность глубже `MAX_CONTEXT_DEPTH`
  (8) превращается в строку. Последующие изменения исключения снимок не
  меняют.
- **`limit` ограничивает объём работы.** Снимается не более `limit`
  исключений (по умолчанию `MAX_EXCEPTIONS`, 253). Если достижимых было
  больше, `truncated` равен `True`, а ссылки на пропущенные исключения — `None`.
- **Локальные переменные — по явному запросу.** При `capture_locals=True`
  каждый кадр хранит `repr` своих локальных переменных, обрезанный до
  `MAX_LOCAL_LENGTH` (200) символов. Переменные с именем любого скрытого поля
  цепочки заменяются на `"<redacted>"`.

Скрытые поля, как и везде, не попадают в `context`. Скрытие локальных
переменных работает по имени и является эвристикой: секрет, скопированный в
переменную с другим именем, будет снят, а `line_text`, буквальные
переопределения сообщения и заметки не фильтруются никогда.

## Проверка типов

`TypedException` декорирован через `dataclass_transform`:

```python
@dataclass_transform(
    kw_only_default=True,
    eq_default=False,
    field_specifiers=(field, dataclasses.field),
)
```

Это даёт анализаторам типов следующее представление:

- объявленные поля становятся именованными параметрами конструктора;
- `message` необязательная строка при конструировании;
- `message` является строкой после конструирования;
- `message_template` необязательная строка при конструировании (шаблон
  экземпляра, см. [Переопределение сообщения](#переопределение-сообщения));
- объявления `field()` и `dataclasses.field()` имеют аннотированный тип;
- методы равенства и упорядочивания dataclass не генерируются.

Анализаторы типов не проверяют значения полей во время выполнения. Используйте
mypy или pyright, чтобы поймать несоответствующие значения:

```python
error = UserNotFound(user_id="42")  # static type error
```

### Как объявлять шаблоны для анализаторов типов

Объявляйте `message_template` в подклассе **без аннотации**, как во всех
примерах этого документа. Поскольку для анализаторов типов `message_template`
ещё и аргумент конструктора, mypy отвергает аннотированные формы:

```python
class Bad(TypedException):
    message_template: ClassVar[str] = "..."  # mypy: Cannot override instance
    #                                          variable with class variable
    message_template: Final = "..."  # mypy: Cannot override writable
    #                                          attribute with a final one
```

Обе формы работают во время выполнения; возражает только статическая проверка.
Обычное `message_template = "..."` принимается и сохраняет тип `str`.

### `MISSING` — удобство времени выполнения

Для анализаторов типов `message` и `message_template` имеют тип `str`, поэтому
`message=MISSING` (описан выше) mypy сообщает как ошибку типа аргумента, хотя
во время выполнения это работает. Лучше опустить аргумент либо отключить
проверку там, где вы намеренно пробрасываете необязательное значение.

Сам проект проверяется через mypy с довольно строгими настройками, включая
`disallow_untyped_defs` и `disallow_any_generics`.

## Справочник по API

### `TypedException`

Базовый класс для всех структурированных исключений.

- Подкласс `Exception`.
- Конструктор только по именам для объявленных полей.
- Необязательный аргумент `message` для буквального переопределения.
- Необязательный аргумент `message_template` для шаблона экземпляра (нельзя
  сочетать с `message`).
- Свойство `context`, возвращающее незарезервированные и нескрытые поля.
- Скомпилированные метаданные и замороженные атрибуты класса.
- Поддержка pickle, копирования, глубокого копирования и заметок.
- Метод `snapshot()`, возвращающий `ExceptionChain` (см.
  [Снимки исключений](#снимки-исключений)).

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

Объявляет поле в подклассе `TypedException`.

- `default`: значение по умолчанию; взаимоисключающе с `factory`.
- `factory`: вызываемый объект без аргументов, создающий свежее значение по
  умолчанию.
- `redact`: скрыть из `context` и запретить в шаблонах.
- `reserved`: скрыть только из `context`.

Вызывает `ValueError`, если заданы и `default`, и `factory`.

### `Field`

Неизменяемая спецификация одного поля исключения.

| Атрибут | Описание |
| --- | --- |
| `name` | Имя поля |
| `default` | Значение по умолчанию или `MISSING` |
| `factory` | Фабрика по умолчанию или `MISSING` |
| `redact` | Скрыто ли значение |
| `reserved` | Зарезервировано ли значение |

### `MISSING`

Сентинел, представляющий пропущенное значение или поле без значения по
умолчанию. Передайте `message=MISSING`, чтобы явно отрисовать шаблон.

```python
from typed_exceptions import MISSING
```

### `Redact`

`Redact[T]` — псевдоним `Annotated[T, ...]`, который помечает поле для скрытия.
Он эквивалентен `field(redact=True)` и наследуется подклассами.

### `snapshot_exception`

```python
snapshot_exception(
    exc,
    *,
    schema_version,
    capture_locals=False,
    limit=253,
)
```

Фиксирует `exc` и всё достижимое от него в виде `ExceptionChain`. См.
[Снимки исключений](#снимки-исключений).

- `schema_version`: метка, сохраняемая в результате; обязательна.
- `capture_locals`: также записывать локальные переменные кадров (скрытые имена
  не сохраняются).
- `limit`: наибольшее число снимаемых исключений; остальное выставляет
  `truncated`.

### `ExceptionChain`

Неизменяемый результат снимка, импортируется из `typed_exceptions.snapshot`.

| Член | Описание |
| --- | --- |
| `items` | `tuple[ExceptionSnapshot, ...]`, первым снятое исключение |
| `schema_version` | Метка, переданная вызывающим кодом |
| `truncated` | Остались ли достижимые исключения за пределами лимита |
| `to_dict()` | Словарь, совместимый с JSON |
| `to_json()` | Строка JSON; неподдерживаемые значения становятся `str()` |

В модуле также определены `MAX_EXCEPTIONS` (253), `MAX_LOCAL_LENGTH` (200) и
`MAX_CONTEXT_DEPTH` (8).

## Справочник ошибок

| Ситуация | Исключение |
| --- | --- |
| Отсутствует обязательное поле | `TypeError` |
| Неизвестный именованный аргумент | `TypeError` |
| Позиционный аргумент конструктора | `TypeError` |
| Переопределение `message` не строкой | `TypeError` |
| Некорректное присваивание `args` | `TypeError` |
| Удаление `message` | `AttributeError` |
| Нехешируемое значение по умолчанию | `ValueError` |
| Одновременно `default` и `factory` | `ValueError` |
| Не-строковый аргумент `message_template=` | `TypeError` |
| Одновременно `message=` и `message_template=` | `TypeError` |
| Некорректный заполнитель шаблона (класса или экземпляра) | `ValueError` |
| Скрытый заполнитель в шаблоне | `ValueError` |
| Некорректный тип `__redact__`, неизвестное имя или `"message"` | `TypeError` |
| Зарезервированное имя поля | `TypeError` |
| `message = ...` в теле класса | `TypeError` |
| `message_template` не строка | `TypeError` |
| Неподдерживаемый параметр `dataclasses.field` | `TypeError` |
| Замороженные метаданные класса | `AttributeError` |
| Замороженный атрибут `Field` | `dataclasses.FrozenInstanceError` (подкласс `AttributeError`) |

## Замечания по дизайну и ограничения

- **Нет проверки типов во время выполнения.** Проверяется наличие полей; типы
  значений полей — ответственность статического анализатора типов.
- **Компиляция происходит при определении класса.** Поля и шаблоны
  компилируются в `__init_subclass__`. Изменение объявлений после создания
  класса не поддерживается.
- **Конструирование только по именам.** Позиционной формы конструктора нет.
- **Только простые шаблоны.** Заполнители должны быть простыми
  идентификаторами; никаких преобразований, спецификаций формата, доступа к
  атрибутам или позиционных заполнителей.
- **`message` зарезервировано.** Используйте `message_template` для шаблонов и
  `message=` для буквальных переопределений.
- **Скрытие не является границей безопасности.** Оно скрывает значения из
  `context` и шаблонов, но явные переопределения сообщения, заметки, вложенные
  значения и данные pickle не фильтруются.
- **Имена маркеров должны разрешаться.** `Redact[T]` в строковой аннотации
  работает, только когда имя видно в пространстве имён класса или модуля.
  Локальные псевдонимы при `from __future__ import annotations` не
  обнаруживаются; для конфиденциальных полей используйте `field(redact=True)`.
- **Перерисовка срабатывает только на присваивания.** Переприсваивание поля
  шаблона обновляет сообщение, а изменение значения на месте — нет.
- **Произвольные атрибуты экземпляра разрешены.** Присваивание неизвестного
  имени создаёт обычный атрибут экземпляра; опечатки не обнаруживаются.
- **Скрытие в снимках работает по именам.** `context` никогда не содержит
  скрытых полей, но `capture_locals=True` скрывает локальные переменные только
  по совпадению с именами скрытых полей. Переименованные копии, `line_text`,
  буквальные сообщения и заметки сохраняются как есть.
- **Снимки имеют вид JSON и не восстанавливаются обратно.** Значения `context`
  приводятся к типам JSON (для остального — `str()`) в момент снимка, а
  исходные исключения по снимку восстановить нельзя.
- **Нет семантики dataclass.** `eq`, упорядочивание и другие генерируемые
  dataclass методы не добавляются.
- **Только Python 3.14+.** Код использует обобщения PEP 695 и обработку
  аннотаций PEP 649.
- **Нет конфигурации CI или pre-commit.** Запускайте проверки вручную.

## Разработка

Установите все группы зависимостей:

```bash
uv sync --all-groups
```

Запустите набор тестов:

```bash
uv run pytest -q
```

Запустите один тест:

```bash
uv run pytest tests/test_exception.py::test_quick_start -q
```

Проверка типов, линтинг, слоёв импортов и форматирования:

```bash
uv run mypy src tests
uv run ruff check src tests
uv run ruff format --check src tests
uv run lint-imports
```

Запустить тесты с покрытием:

```bash
uv run pytest --cov
```

Применить форматирование:

```bash
uv run ruff format src tests
```

Собрать пакет:

```bash
uv build
```

Примечания:

- Обычный `uv sync` устанавливает только группу `dev` и удаляет группу `test`.
  Используйте `--all-groups`, чтобы установить pytest и инструменты покрытия.
- Тесты находятся в одном файле, `tests/test_exception.py`.
- Набор тестов также запускает doctest в docstring класса `TypedException`.
- Ruff проверяет docstring в стиле Google через правила `D` с
  `convention = "google"`.
- mypy настроен с довольно строгими параметрами; аннотируйте каждую функцию,
  включая тесты.
- `lint-imports` проверяет порядок слоёв пакета, заданный в `pyproject.toml`.
- Конфигурации CI или pre-commit нет; запускайте команды выше вручную.

## Структура проекта

```text
typed_exceptions/
├── src/
│   └── typed_exceptions/
│       ├── __init__.py           # public API: TypedException, field, Field,
│       │                         # MISSING, Redact, snapshot_exception
│       ├── exception.py          # runtime, metaclass, class construction
│       ├── snapshot.py           # exception chain snapshots
│       ├── field.py              # Field specifications, field(), dataclass interop
│       ├── template.py           # template compilation and rendering
│       ├── introspection.py      # PEP 649 annotation helpers
│       ├── access.py             # Redact marker
│       ├── sentinel.py           # MISSING sentinel
│       └── py.typed
├── tests/
│   └── test_exception.py
├── LICENSE
├── pyproject.toml
├── README.md
├── README.ru.md
└── uv.lock
```

## Лицензия

В репозитории есть файл `LICENSE` с лицензией MIT. Перед распространением
пакета замените в нём шаблон `[fullname]`.
