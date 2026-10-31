"""
Tests for tokeo.core.logic: the package and its modules load from tokeo next
to tokeo.core.exc of tokeo-base, and ```TokeoLogicError``` is a
```TokeoError```, so the handlers of tokeo errors catch it as well.
"""

import os
import pickle
import importlib

import pytest

from tokeo.core.exc import TokeoBaseException, TokeoError
from tokeo.core.logic.exc import TokeoLogicError
from tests.utils import ROOT

# tokeo.core.logic


@pytest.mark.parametrize('module', ['tokeo.core.logic', 'tokeo.core.logic.exc', 'tokeo.core.logic.utils'])
def test_logic_modules_load_from_the_tokeo_package(module):
    """The package and its modules load from tokeo/core/logic of the project."""
    path = importlib.import_module(module).__file__
    assert os.path.dirname(path) == os.path.join(ROOT, 'tokeo', 'core', 'logic')


# TokeoLogicError


def test_tokeo_logic_error_order_of_bases():
    """TokeoLogicError looks up TokeoError, Exception, then TokeoBaseException."""
    assert TokeoLogicError.__mro__ == (TokeoLogicError, TokeoError, Exception, TokeoBaseException, BaseException, object)


@pytest.mark.parametrize('base', [TokeoError, Exception, TokeoBaseException])
def test_tokeo_logic_error_is_caught_by_its_bases(base):
    """except TokeoError, Exception and TokeoBaseException all catch it."""
    with pytest.raises(base):
        raise TokeoLogicError('x')


def test_tokeo_logic_error_keeps_its_message():
    """The message is the first arg and the text of the error."""
    error = TokeoLogicError('no valid state')
    assert error.args == ('no valid state',)
    assert str(error) == 'no valid state'


def test_tokeo_logic_error_pickles():
    """A pickled TokeoLogicError comes back with its class and args."""
    error = pickle.loads(pickle.dumps(TokeoLogicError('a', 'b')))
    assert type(error) is TokeoLogicError and error.args == ('a', 'b')
