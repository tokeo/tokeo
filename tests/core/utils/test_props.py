"""
Tests for tokeo.core.utils.props: the functions find and read an attribute of
an object or a key of a dict or container by one call.
"""

from collections import UserDict

import pytest

from tokeo.core.utils.props import hasprop, hasprops, anyprop, getprop


class Sample:
    """An object with the attributes a and b."""

    a = 1
    b = 2


class NoContains:
    """A container whose membership test fails."""

    def __contains__(self, name):
        raise TypeError('no membership')


def test_hasprop():
    """An attribute, a key or an entry is found, else False."""
    assert hasprop(Sample(), 'a') is True
    assert hasprop({'a': 1}, 'a') is True
    assert hasprop({'a': 1}, 'b') is False
    assert hasprop(['a'], 'a') is True
    assert hasprop(NoContains(), 'a') is False
    assert hasprop(1, 'a') is False


def test_hasprops():
    """All names must be found, an empty list of names passes."""
    assert hasprops(Sample(), ('a', 'b')) is True
    assert hasprops(Sample(), ('a', 'c')) is False
    assert hasprops(Sample(), ()) is True
    assert hasprops({'a': 1, 'b': 2}, ('a', 'b')) is True
    assert hasprops({'a': 1}, ('a', 'b')) is False
    assert hasprops(NoContains(), ('a',)) is False


def test_anyprop():
    """One found name is enough."""
    assert anyprop(Sample(), ('c', 'b')) is True
    assert anyprop(Sample(), ('c', 'd')) is False
    assert anyprop({'a': 1}, ('c', 'a')) is True
    assert anyprop({'a': 1}, ('c',)) is False
    assert anyprop(NoContains(), ('a',)) is False


def test_getprop():
    """An attribute or a key is read, a missing one gives the default."""
    assert getprop(Sample(), 'a') == 1
    assert getprop({'a': 1}, 'a') == 1
    assert getprop(UserDict(a=1), 'a') == 1
    assert getprop({'a': 1}, 'b', default=0) == 0
    assert getprop({'a': 1}, 'b', fallback=0) == 0
    assert getprop(Sample(), 'c', fallback=None) is None
    assert getprop(NoContains(), 'a', default=0) == 0
    assert getprop(['a'], 'b', default=0) == 0
    assert getprop(1, 'a', default=0) == 0


@pytest.mark.parametrize('obj', [Sample(), {'a': 1}, ['a'], NoContains(), 1])
def test_getprop_without_default_raises(obj):
    """A missing name without a default raises an AttributeError."""
    with pytest.raises(AttributeError, match="no attribute or key 'c'"):
        getprop(obj, 'c')
