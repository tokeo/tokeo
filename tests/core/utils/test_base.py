"""
Tests for tokeo.core.utils.base, the value helpers.

```as_int_or_auto``` lets a config key or an argument take a number or
```'auto'```. These pin the accepted forms, the lower bound and the errors.
"""

import pytest

from tokeo.core.utils.base import as_int_or_auto


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
