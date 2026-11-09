from typing import Any

from better_exceptions.exception import BetterException


class IsNotExpectedExceptionClass[
    Expected: BetterException,
](BetterException):
    exception_type: type[BetterException]
    expected_type: type[Expected]


class IsNotClass(BetterException):
    message_template = "{value} is not class"
    value: Any
