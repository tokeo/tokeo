"""
Tests for tokeo.core.utils.base: the functions hand back a value, or a default
when it is missing, empty or blank; the string forms refuse other types by a
TypeError.
"""

import pytest

from tokeo.core.utils.base import (
    value_or_default,
    filled_str_or_default,
    first_value_or_default,
    first_filled_str_or_default,
)


@pytest.mark.parametrize('value', [None, '', '  ', [], {}, (), set()])
def test_value_or_default_takes_the_default_for_empty(value):
    """None, a blank string and an empty container give the default."""
    assert value_or_default(value, 'x') == 'x'


@pytest.mark.parametrize('value', ['a', [0], {'a': 1}, 0, False])
def test_value_or_default_keeps_a_filled_value(value):
    """A filled string or container and any other value come back."""
    assert value_or_default(value, 'x') is value


@pytest.mark.parametrize('value, expected', [(None, 'x'), ('', 'x'), (' \t', 'x'), ('a', 'a'), (' a ', ' a ')])
def test_filled_str_or_default(value, expected):
    """None and a blank string give the default, a filled one comes back."""
    assert filled_str_or_default(value, 'x') == expected


def test_filled_str_or_default_refuses_other_types():
    """A value that is no string raises a TypeError."""
    with pytest.raises(TypeError, match='filled_str_or_default expects a string'):
        filled_str_or_default(1, 'x')


def test_first_value_or_default():
    """The first value that is not None comes back, else the default."""
    assert first_value_or_default([None, 0, 1], 'x') == 0
    assert first_value_or_default((None, None), 'x') == 'x'
    assert first_value_or_default([]) is None
    with pytest.raises(TypeError, match='first_value_or_default expects a list or tuple'):
        first_value_or_default('ab')


def test_first_filled_str_or_default():
    """The first filled string comes back, else the default."""
    assert first_filled_str_or_default([None, ' ', 'a', 'b'], 'x') == 'a'
    assert first_filled_str_or_default(('', None), 'x') == 'x'
    with pytest.raises(TypeError, match='first_filled_str_or_default expects a list or tuple'):
        first_filled_str_or_default('ab')
    with pytest.raises(TypeError, match='first_filled_str_or_default expects strings'):
        first_filled_str_or_default([None, 1])
