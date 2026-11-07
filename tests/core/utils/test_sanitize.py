"""
Tests for tokeo.core.utils.sanitize: filter_keyword_keys keeps the names that are
keywords and their values as they are, quote_keyword_dict quotes the values too.
"""

import pytest

from tokeo.core.utils.sanitize import quote_keyword_dict, filter_keyword_keys

ENV = {'GREETING': 'hello world', 'BAD-NAME': 'x', 'A;rm': 'y'}


def test_filter_keyword_keys_drops_bad_names_and_keeps_the_values():
    """Only keyword names stay, their values unchanged."""
    assert filter_keyword_keys(ENV) == {'GREETING': 'hello world'}


def test_quote_keyword_dict_quotes_the_values_of_the_keyword_names():
    """The values of the keyword names are quoted for a shell."""
    assert quote_keyword_dict(ENV) == {'GREETING': "'hello world'"}


@pytest.mark.parametrize('function', [filter_keyword_keys, quote_keyword_dict])
def test_none_passes_and_no_mapping_raises(function):
    """None passes through, a value without items() raises ValueError."""
    assert function(None) is None
    with pytest.raises(ValueError, match='No iterable dict'):
        function(['GREETING'])
