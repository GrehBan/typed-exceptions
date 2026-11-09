import inspect

from better_exceptions.exception import BetterException
from better_exceptions.validator import exceptions
from better_exceptions.validator.many import (
    ManyFilter,
    ManyValidator,
)
from better_exceptions.validator.protocols import IValidator


class IsClass(IValidator[[object], bool]):
    @classmethod
    def validate(cls, obj: object) -> bool:
        return inspect.isclass(obj)


class IsClassMany(ManyValidator):
    @classmethod
    def validate(
        cls, *objects: object, filter: ManyFilter = ManyFilter.ALL
    ) -> bool:
        checker = cls.checker(filter)

        return checker(IsClass.validate(obj) for obj in objects)


class IsSubclass(
    IValidator[
        [type[BetterException], type[BetterException]],
        bool,
    ]
):
    @classmethod
    def validate(
        cls,
        exception_type: type[BetterException],
        expected_type: type[BetterException],
    ) -> bool:
        if not IsClassMany.validate(exception_type, expected_type):
            raise exceptions.IsNotClass(
                value=f"{exception_type.__name__} and {expected_type.__name__}"
            )
        return issubclass(exception_type, expected_type)


class IsNotExpectedExceptionClass[Expected: BetterException](
    IValidator[
        [
            type[BetterException],
            type[Expected],
            type[exceptions.IsNotExpectedExceptionClass[Expected]],
        ],
        exceptions.IsNotExpectedExceptionClass[Expected] | None,
    ]
):
    @classmethod
    def validate(
        cls,
        exception_type: type[BetterException],
        expected_type: type[Expected],
        returns: type[exceptions.IsNotExpectedExceptionClass[Expected]],
    ) -> exceptions.IsNotExpectedExceptionClass[Expected] | None:
        if not IsSubclass.validate(
            exception_type=exception_type, expected_type=expected_type
        ):
            return returns(
                exception_type=exception_type, expected_type=expected_type
            )
        return None
