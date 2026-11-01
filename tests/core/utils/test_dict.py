"""
Tests for tokeo.core.utils.dict: deep_merge merges dicts key by key, appends
lists and replaces every other value as a whole, also a date or a VaultRef of
another config file; its errors name types, the values go to the debug log.
"""

import logging
from datetime import date

import pytest

from tokeo.core.utils.dict import deep_merge
from tokeo.core.vaults import VaultRef


def test_deep_merge_merges_dicts_and_appends_lists():
    """Keys of b go into a, nested dicts merge, lists append."""
    a = {'db': {'user': 'shop', 'port': 1}, 'hosts': ['a']}
    b = {'db': {'port': 2, 'name': 'x'}, 'hosts': ['b']}
    assert deep_merge(a, b) == {'db': {'user': 'shop', 'port': 2, 'name': 'x'}, 'hosts': ['a', 'b']}


@pytest.mark.parametrize(
    'old, new',
    [
        (VaultRef('master', 'gAAA'), VaultRef('master', 'gBBB')),
        (date(2026, 1, 1), date(2026, 2, 1)),
        ('text', VaultRef('master', 'gBBB')),
        (VaultRef('master', 'gAAA'), 'text'),
    ],
    ids=['vaultref', 'date', 'str_by_vaultref', 'vaultref_by_str'],
)
def test_deep_merge_replaces_a_single_value(old, new):
    """A value that is no list and no dict is replaced by the one of b."""
    assert deep_merge({'db': {'password': old}}, {'db': {'password': new}}) == {'db': {'password': new}}


def test_deep_merge_refuses_a_value_for_a_dict_and_names_the_values_at_debug(caplog):
    """A dict takes no single value, the error names types, the debug log values."""
    with caplog.at_level(logging.DEBUG, logger='tokeo.core.utils.dict'):
        with pytest.raises(ValueError, match='^Cannot merge a str into a dict$'):
            deep_merge({'db': {'user': 'shop'}}, {'db': 's3cret'})
    assert "deep_merge: cannot merge 's3cret' into {'user': 'shop'}" in caplog.messages
