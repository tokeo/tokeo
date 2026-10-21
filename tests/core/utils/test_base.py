"""
Tests for the value helpers in the base utils.

```as_int_or_auto``` lets a config key or an argument take a number or
```'auto'```. These pin the accepted forms, the lower bound and the errors.
"""

import pytest

from tokeo.core.utils.base import as_int_or_auto


@pytest.mark.parametrize('value', ['auto', 'AUTO', ' Auto '])
def test_as_int_or_auto_keyword(value):
    assert as_int_or_auto(value) == 'auto'


@pytest.mark.parametrize('value, expected', [(1, 1), (10, 10), ('10', 10), (' 7 ', 7)])
def test_as_int_or_auto_number(value, expected):
    result = as_int_or_auto(value)
    assert result == expected
    assert type(result) is int


def test_as_int_or_auto_min_value():
    assert as_int_or_auto(0, min_value=0) == 0
    assert as_int_or_auto('0', min_value=0) == 0
    with pytest.raises(ValueError, match='integer >= 1'):
        as_int_or_auto(0)
    with pytest.raises(ValueError, match='integer >= 0'):
        as_int_or_auto(-1, min_value=0)


@pytest.mark.parametrize('value', [None, True, False, 2.0, 2.5, '', 'x', '2.5', 'automatic', [1]])
def test_as_int_or_auto_rejects(value):
    with pytest.raises(ValueError, match='must be "auto" or an integer'):
        as_int_or_auto(value)
