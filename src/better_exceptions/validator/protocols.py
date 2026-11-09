from typing import Protocol

from better_exceptions.validator.many import ManyFilter


class IValidator[**P, R](Protocol):
    @classmethod
    def validate(cls, *args: P.args, **kwargs: P.kwargs) -> R: ...


class IManyValidator(Protocol):
    @classmethod
    def validate(
        cls, *objects: object, filter: ManyFilter = ManyFilter.ALL
    ) -> bool: ...
