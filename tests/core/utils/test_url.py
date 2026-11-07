"""
Tests for tokeo.core.utils.url: format_auth_url puts identity and password
into a url, encoded, and keeps an ipv6 address in its brackets.
"""

import pytest

from tokeo.core.utils.url import format_auth_url


@pytest.mark.parametrize(
    'url, expected',
    [
        ('amqp://host:5672/vh', 'amqp://u:p@host:5672/vh'),
        ('amqp://old:pw@host/vh', 'amqp://u:p@host/vh'),
        ('amqp://[::1]:5672/vh', 'amqp://u:p@[::1]:5672/vh'),
        ('amqps://[2001:db8::1]/vh?heartbeat=30', 'amqps://u:p@[2001:db8::1]/vh?heartbeat=30'),
    ],
    ids=['ipv4', 'replaces_userinfo', 'ipv6_with_port', 'ipv6_with_query'],
)
def test_format_auth_url_sets_identity_and_password(url, expected):
    """The given identity and password win, the host keeps its form."""
    assert format_auth_url(url, 'u', 'p') == expected


def test_format_auth_url_encodes_the_userinfo_and_keeps_none():
    """A special character is encoded, no url gives None."""
    assert format_auth_url('amqp://host/', 'u@x', 'p:w/') == 'amqp://u%40x:p%3Aw%2F@host/'
    assert format_auth_url(None, 'u', 'p') is None
