from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from enum import StrEnum, auto, unique
from typing import Any

from better_exceptions.exception import BetterException

type ManyChecker = Callable[[Iterable[object]], bool]


@unique
class ManyFilter(StrEnum):
    ALL = auto()
    ANY = auto()


def many_checker(filter: ManyFilter, called: str) -> ManyChecker:
    match filter:
        case ManyFilter.ALL:
            return all
        case ManyFilter.ANY:
            return any
        case _:
            raise UnknownManyFilter(
                expected=ManyFilter, value=filter, called=called
            )


class UnknownManyFilter(BetterException):
    message_template = "`{called}` filter should be a {expected}, got {value}"
    called: str
    expected: type[ManyFilter]
    value: Any


class ManyValidator(ABC):
    @classmethod
    @abstractmethod
    def validate(
        cls, *objects: object, filter: ManyFilter = ManyFilter.ALL
    ) -> bool:
        raise NotImplementedError

    @classmethod
    def checker(cls, filter: ManyFilter) -> ManyChecker:
        return many_checker(filter=filter, called=cls.__name__)
