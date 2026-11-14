"""Pure unit tests; these do not demonstrate a verified task lifecycle."""

import pytest

from backend.security.deterministic_task_policy import AdditionRequest, MAX_ABS_OPERAND


@pytest.mark.parametrize("left,right,expected", [(1, 2, 3), (-1, 1, 0), (MAX_ABS_OPERAND, MAX_ABS_OPERAND, 2 * MAX_ABS_OPERAND)])
def test_addition_is_deterministic(left, right, expected):
    request = AdditionRequest(left, right)
    assert request.execute() == expected
    assert request.digest() == AdditionRequest(left, right).digest()


@pytest.mark.parametrize("value", [True, False, 1.0, "1", None, [], {}])
def test_operands_reject_non_integer_values(value):
    with pytest.raises(TypeError):
        AdditionRequest(value, 1)


@pytest.mark.parametrize("value", [MAX_ABS_OPERAND + 1, -MAX_ABS_OPERAND - 1])
def test_operands_are_bounded(value):
    with pytest.raises(ValueError):
        AdditionRequest(1, value)


def test_argument_mutation_changes_digest_and_record_is_detached():
    request = AdditionRequest(1, 2)
    digest = request.digest()
    exported = request.canonical_record()
    exported["arguments"]["left"] = 5
    assert request.digest() == digest
    assert AdditionRequest(5, 2).digest() != digest


def test_request_is_immutable():
    request = AdditionRequest(1, 2)
    with pytest.raises(AttributeError):
        request.left = 3
