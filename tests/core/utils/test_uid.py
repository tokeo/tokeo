"""
Tests for tokeo.core.utils.uid: ```get_token_hex``` and ```get_uuid4``` hand
out a random token, optionally prefixed; the tests pin the length, the prefix
behaviour and the type check.
"""

import pytest

from tokeo.core.utils.uid import get_token_hex, get_uuid4


def test_token_hex_length_follows_bytes():
    """The hex string is twice as long as the bytes asked for."""
    assert len(get_token_hex(4)) == 8
    assert len(get_token_hex(8)) == 16
    assert len(get_token_hex(16)) == 32


def test_token_hex_default_is_eight_bytes():
    """Without a size the token has 8 bytes, 16 hex chars."""
    assert len(get_token_hex()) == 16


def test_token_hex_prefix_forms():
    """A prefix leads the token, None or "" give the bare token."""
    assert get_token_hex(4, 'inj_').startswith('inj_')
    assert len(get_token_hex(4, 'inj_')) == len('inj_') + 8
    # None or '' -> bare token, no prefix
    assert '_' not in get_token_hex(4)
    assert '_' not in get_token_hex(4, '')


def test_token_hex_rejects_non_str_prefix():
    """A prefix that is no str raises TypeError."""
    with pytest.raises(TypeError):
        get_token_hex(4, 5)


def test_token_hex_is_random():
    """Two tokens of 4 bytes differ."""
    # two draws must differ (collision at 32 bit is ~2.3e-10)
    assert get_token_hex(4) != get_token_hex(4)


def test_uuid4_hex_no_dashes_and_prefix():
    """The uuid4 is 32 hex chars without dashes, a prefix leads it."""
    u = get_uuid4()
    assert len(u) == 32
    assert '-' not in u
    assert get_uuid4('inj_').startswith('inj_')
    # None or '' -> bare hex
    assert '_' not in get_uuid4('')


def test_uuid4_rejects_non_str_prefix():
    """A prefix that is no str raises TypeError."""
    with pytest.raises(TypeError):
        get_uuid4(5)
