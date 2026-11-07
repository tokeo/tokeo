"""
Tests for tokeo.core.utils.base: the functions hand back a value, or a default
when it is missing, empty or blank; the string forms refuse other types by a
TypeError.
"""

import pytest

from tokeo.core.utils.base import (
    get_value_or_default,
    get_filled_str_or_default,
    get_first_value_or_default,
    get_first_filled_str_or_default,
)


@pytest.mark.parametrize('value', [None, '', '  ', [], {}, (), set()])
def test_get_value_or_default_takes_the_default_for_empty(value):
    """None, a blank string and an empty container give the default."""
    assert get_value_or_default(value, 'x') == 'x'


@pytest.mark.parametrize('value', ['a', [0], {'a': 1}, 0, False])
def test_get_value_or_default_keeps_a_filled_value(value):
    """A filled string or container and any other value come back."""
    assert get_value_or_default(value, 'x') is value


@pytest.mark.parametrize('value, expected', [(None, 'x'), ('', 'x'), (' \t', 'x'), ('a', 'a'), (' a ', ' a ')])
def test_get_filled_str_or_default(value, expected):
    """None and a blank string give the default, a filled one comes back."""
    assert get_filled_str_or_default(value, 'x') == expected


def test_get_filled_str_or_default_refuses_other_types():
    """A value that is no string raises a TypeError."""
    with pytest.raises(TypeError, match='get_filled_str_or_default expects a string'):
        get_filled_str_or_default(1, 'x')


def test_get_first_value_or_default():
    """The first value that is not None comes back, else the default."""
    assert get_first_value_or_default([None, 0, 1], 'x') == 0
    assert get_first_value_or_default((None, None), 'x') == 'x'
    assert get_first_value_or_default([]) is None
    with pytest.raises(TypeError, match='get_first_value_or_default expects a list or tuple'):
        get_first_value_or_default('ab')


def test_get_first_filled_str_or_default():
    """The first filled string comes back, else the default."""
    assert get_first_filled_str_or_default([None, ' ', 'a', 'b'], 'x') == 'a'
    assert get_first_filled_str_or_default(('', None), 'x') == 'x'
    with pytest.raises(TypeError, match='get_first_filled_str_or_default expects a list or tuple'):
        get_first_filled_str_or_default('ab')
    with pytest.raises(TypeError, match='get_first_filled_str_or_default expects strings'):
        get_first_filled_str_or_default([None, 1])
