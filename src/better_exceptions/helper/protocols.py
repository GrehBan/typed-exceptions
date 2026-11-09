from typing import Protocol


class IHelper[**P, R](Protocol):
    @classmethod
    def validate(cls, *args: P.args, **kwargs: P.kwargs) -> R: ...
