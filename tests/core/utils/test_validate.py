"""
Tests for tokeo.core.utils.validate: the checks tell by True or False whether
a value fits, refuse bool, inf and nan as number, never convert a string and
never raise. ```valid_or_default``` hands back the value or a default,
```as_int_or_auto``` and ```as_list``` convert a value into one form.
"""

import pytest

from tokeo.core.utils.validate import (
    as_int_or_auto,
    as_list,
    is_basetype,
    is_bool,
    is_int,
    is_int_gt,
    is_int_ge,
    is_int_between,
    is_number,
    is_number_gt,
    is_number_ge,
    is_number_between,
    is_filled_str,
    is_list,
    is_list_of,
    valid_or_default,
)

# values that are never a number
NO_NUMBERS = [True, False, None, '1', '0.05', [1], {}, float('inf'), float('-inf'), float('nan')]


@pytest.mark.parametrize('value', NO_NUMBERS)
@pytest.mark.parametrize(
    'check',
    [
        is_int,
        is_number,
        lambda value: is_int_gt(value, 0),
        lambda value: is_int_ge(value, 0),
        lambda value: is_int_between(value, 0, 10),
        lambda value: is_number_gt(value, 0),
        lambda value: is_number_ge(value, 0),
        lambda value: is_number_between(value, 0, 10),
    ],
)
def test_no_number_passes_a_number_check(check, value):
    """bool, None, strings, containers, inf and nan fail every number check."""
    assert check(value) is False


@pytest.mark.parametrize('value', [1, 0, 'true', 'false', None, []])
def test_is_bool_takes_true_and_false_only(value):
    """is_bool is True for True and False, not for 1, 0, a string or None."""
    assert is_bool(True) is True
    assert is_bool(False) is True
    assert is_bool(value) is False


def test_is_int_takes_int_only():
    """is_int is True for an int, not for a float."""
    assert is_int(0) is True
    assert is_int(-3) is True
    assert is_int(1.0) is False


def test_is_number_takes_int_and_float():
    """is_number is True for an int and a float."""
    assert is_number(0) is True
    assert is_number(-3) is True
    assert is_number(0.05) is True
    # a huge int is a number, the check must not overflow
    assert is_number(10**400) is True


def test_int_limits():
    """gt excludes the limit, ge and between include it."""
    assert is_int_gt(1, 0) is True
    assert is_int_gt(0, 0) is False
    assert is_int_ge(0, 0) is True
    assert is_int_ge(-1, 0) is False
    assert is_int_between(1, 1, 3) is True
    assert is_int_between(3, 1, 3) is True
    assert is_int_between(0, 1, 3) is False
    assert is_int_between(4, 1, 3) is False
    assert is_int_gt(2.0, 1) is False


def test_number_limits():
    """gt excludes the limit, ge and between include it."""
    assert is_number_gt(0.001, 0) is True
    assert is_number_gt(0, 0) is False
    assert is_number_ge(1, 1) is True
    assert is_number_ge(0.99, 1) is False
    assert is_number_between(1, 1, 86400) is True
    assert is_number_between(86400.0, 1, 86400) is True
    assert is_number_between(0.5, 1, 86400) is False
    assert is_number_between(86401, 1, 86400) is False


@pytest.mark.parametrize('value, expected', [('a', True), (' a ', True), ('', False), ('  \t\n', False), (None, False), (1, False)])
def test_is_filled_str(value, expected):
    """A string with more than whitespace passes, all else fails."""
    assert is_filled_str(value) is expected


@pytest.mark.parametrize('value, expected', [([], True), ((), True), ([1], True), ('ab', False), ({}, False), (None, False)])
def test_is_list(value, expected):
    """A list or a tuple passes, also an empty one, a string does not."""
    assert is_list(value) is expected


def test_is_list_of():
    """All entries must pass the check, an empty list passes."""
    assert is_list_of(['a', 'b'], is_filled_str) is True
    assert is_list_of(('a',), is_filled_str) is True
    assert is_list_of([], is_filled_str) is True
    assert is_list_of(['a', ' '], is_filled_str) is False
    assert is_list_of('ab', is_filled_str) is False
    assert is_list_of([1, 2], lambda entry: is_int_gt(entry, 0)) is True
    assert is_list_of([1, True], lambda entry: is_int_gt(entry, 0)) is False


@pytest.mark.parametrize('value', [1, 'a', 1.5, True, b'a', [], {}, set(), ()])
def test_is_basetype_takes_the_base_types(value):
    """int, str, float, bool, bytes, list, dict, set and tuple are base types."""
    assert is_basetype(value) is True


@pytest.mark.parametrize('value', [None, object(), frozenset(), len])
def test_is_basetype_refuses_other_types(value):
    """None, objects, functions and other types are no base types."""
    assert is_basetype(value) is False


def test_valid_or_default():
    """A fitting value comes back, else the default, None by default."""
    assert valid_or_default(['a'], is_list, []) == ['a']
    assert valid_or_default(None, is_list, []) == []
    assert valid_or_default('a', is_list) is None
    assert valid_or_default(0.5, lambda value: is_number_gt(value, 0), 1) == 0.5
    assert valid_or_default(True, lambda value: is_number_gt(value, 0), 1) == 1


@pytest.mark.parametrize('value', ['auto', 'AUTO', ' Auto '])
def test_as_int_or_auto_keyword(value):
    """ "auto" in any case and with blanks comes back as "auto"."""
    assert as_int_or_auto(value) == 'auto'


@pytest.mark.parametrize('value, expected', [(1, 1), (10, 10), ('10', 10), (' 7 ', 7)])
def test_as_int_or_auto_number(value, expected):
    """An int or a numeric string comes back as int."""
    result = as_int_or_auto(value)
    assert result == expected
    assert type(result) is int


def test_as_int_or_auto_min_value():
    """0 passes with min_value=0, else values below 1 raise ValueError."""
    assert as_int_or_auto(0, min_value=0) == 0
    assert as_int_or_auto('0', min_value=0) == 0
    with pytest.raises(ValueError, match='integer >= 1'):
        as_int_or_auto(0)
    with pytest.raises(ValueError, match='integer >= 0'):
        as_int_or_auto(-1, min_value=0)


@pytest.mark.parametrize('value', [None, True, False, 2.0, 2.5, '', 'x', '2.5', 'automatic', [1]])
def test_as_int_or_auto_rejects(value):
    """None, bools, floats and other strings raise ValueError."""
    with pytest.raises(ValueError, match='must be "auto" or an integer'):
        as_int_or_auto(value)


@pytest.mark.parametrize('value, expected', [(None, []), ([], []), (['a', 'b'], ['a', 'b']), (('a',), ['a']), ('ab', ['ab']), (0, [0])])
def test_as_list(value, expected):
    """None is an empty list, a list or tuple a list, one value a list of it."""
    assert as_list(value) == expected


def test_as_list_hands_back_a_new_list():
    """The list is a copy, a change does not touch the given list."""
    given = ['a']
    result = as_list(given)
    result.append('b')
    assert given == ['a']
