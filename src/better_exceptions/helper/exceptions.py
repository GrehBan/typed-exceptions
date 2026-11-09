from better_exceptions.exception import BetterException
from better_exceptions.validator import exceptions


class RequireType(BetterException):
    message_template = "{name} must be {expected}"
    name: str
    expected: str


class IsNotRequireType(exceptions.IsNotExpectedExceptionClass[RequireType]):
    message_template = (
        "{exception_type} should be a sublcass of {expected_type}"
    )
    expected_type = RequireType