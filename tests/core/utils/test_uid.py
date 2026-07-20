"""
Tests for the random-id helpers.

```get_token_hex``` and ```get_uuid4``` hand out a random token, optionally
prefixed. These pin the length, the prefix behaviour and the type check.
"""

import pytest

from tokeo.core.utils.uid import get_token_hex, get_uuid4


def test_token_hex_length_follows_bytes():
    # the hex string is twice the byte count
    assert len(get_token_hex(4)) == 8
    assert len(get_token_hex(8)) == 16
    assert len(get_token_hex(16)) == 32


def test_token_hex_default_is_eight_bytes():
    assert len(get_token_hex()) == 16


def test_token_hex_prefix_forms():
    assert get_token_hex(4, 'inj_').startswith('inj_')
    assert len(get_token_hex(4, 'inj_')) == len('inj_') + 8
    # None or '' -> bare token, no prefix
    assert '_' not in get_token_hex(4)
    assert '_' not in get_token_hex(4, '')


def test_token_hex_rejects_non_str_prefix():
    with pytest.raises(TypeError):
        get_token_hex(4, 5)


def test_token_hex_is_random():
    # two draws must differ (collision at 32 bit is ~1e-7)
    assert get_token_hex(4) != get_token_hex(4)


def test_uuid4_hex_no_dashes_and_prefix():
    u = get_uuid4()
    assert len(u) == 32
    assert '-' not in u
    assert get_uuid4('inj_').startswith('inj_')
    # None or '' -> bare hex
    assert '_' not in get_uuid4('')


def test_uuid4_rejects_non_str_prefix():
    with pytest.raises(TypeError):
        get_uuid4(5)
