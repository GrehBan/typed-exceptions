from better_exceptions.helper import exceptions
from better_exceptions.helper.protocols import IHelper
from better_exceptions.validator.class_validators import (
    IsNotExpectedExceptionClass,
)


class RequireType[T](
    IHelper[
        [
            object,
            type[T],
            str,
            type[exceptions.RequireType]
        ],
        None
    ]
):
    @classmethod
    def validate(
        cls,
        value: object,
        expected: type[T],
        name: str,
        exception_type: type[exceptions.RequireType] = exceptions.RequireType
    ) -> None:
        invalid = IsNotExpectedExceptionClass[exceptions.RequireType].validate(
            exception_type,
            exceptions.RequireType,
            exceptions.IsNotRequireType
        )
        if invalid is not None:
            raise invalid
        if not isinstance(value, expected):
            raise exception_type(
                name=name,
                expected=expected.__name__,
            )
