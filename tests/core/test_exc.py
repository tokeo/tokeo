"""
Tests for tokeo.core.exc: ```TokeoError``` stays an Exception and is also a
```TokeoBaseException```; an exception that only derives from the base class
passes ```except Exception``` and is caught by ```except TokeoBaseException```.
"""

import pickle

import pytest

from tokeo.core.exc import TokeoBaseException, TokeoError


class OnlyBase(TokeoBaseException):
    """A tokeo exception that is no Exception, like a time limit abort."""


class TokeoOSError(TokeoError, OSError):
    """A tokeo error together with a builtin error of an own layout."""


def test_tokeo_error_order_of_bases():
    """TokeoError looks up Exception before TokeoBaseException."""
    assert TokeoError.__mro__ == (TokeoError, Exception, TokeoBaseException, BaseException, object)


def test_tokeo_error_is_caught_as_exception_and_base():
    """except Exception and except TokeoBaseException both catch TokeoError."""
    with pytest.raises(Exception):
        raise TokeoError('x')
    with pytest.raises(TokeoBaseException):
        raise TokeoError('x')


def test_only_base_passes_except_exception():
    """An exception only of the base class passes except Exception."""
    with pytest.raises(TokeoBaseException):
        try:
            raise OnlyBase('x')
        except Exception:
            pytest.fail('except Exception caught it')


def test_tokeo_error_with_a_builtin_error_pickles():
    """TokeoError together with OSError is defined and pickles."""
    error = TokeoOSError('a', 'b')
    assert pickle.loads(pickle.dumps(error)).args == ('a', 'b')
    assert isinstance(error, OSError) and isinstance(error, TokeoBaseException)


def test_base_before_tokeo_error_fails_on_definition():
    """The bases TokeoBaseException, TokeoError give no consistent order."""
    with pytest.raises(TypeError, match='consistent method resolution'):

        class Wrong(TokeoBaseException, TokeoError):
            pass
