"""
Tests for the listener settings of the server.

```hosts '*'``` binds the wildcard addresses (0.0.0.0, and :: when IPv6 is
configured) and names the local host in the test certificate instead of
them. ```ports``` and ```hosts``` drop empty items at the end like Ruby's
split. ```io_buffer_max_size``` takes None as the default and refuses
anything but a positive integer.
"""

import socket
import asyncio

import pytest

from tokeo.core.smtpd.server import SmtpdServer, _resolve_host, _tls_cert_names, DEFAULT_IO_BUFFER_MAX_SIZE
from tokeo.core.smtpd.events import SmtpdEvents


def test_wildcard_host_resolves_to_the_wildcard_addresses():
    """'*' resolves to 0.0.0.0 and, with IPv6 configured, ::."""
    ips = _resolve_host('*')
    assert ips and set(ips) <= {'0.0.0.0', '::'}


def test_wildcard_host_names_the_local_host_in_the_certificate():
    """With '*' the SAN names the local host, not the wildcard addresses."""
    cn, sans = _tls_cert_names(None, None, ['*'], [('0.0.0.0', 2525), ('::', 2525)])
    assert cn == 'localhost.local'
    assert {'localhost', '127.0.0.1', '::1', socket.gethostname()} <= set(sans)
    assert '0.0.0.0' not in sans and '::' not in sans and '*' not in sans


def test_wildcard_host_binds_all_interfaces():
    """start() with '*' binds the wildcard address and logs it."""
    lines = []

    class Lines(SmtpdEvents):

        def on_logging_event(self, ctx, severity, msg, err=None):
            lines.append(msg)

    async def go():
        server = SmtpdServer(Lines(), hosts='*', ports=0)
        await server.start()
        bound = [sock.getsockname()[0] for listener in server._servers for sock in listener.sockets]
        await server.stop(wait_seconds_before_close=0)
        return bound

    bound = asyncio.run(go())
    assert '0.0.0.0' in bound
    assert any(line.startswith('Service listening on 0.0.0.0:') for line in lines)


@pytest.mark.parametrize(
    'ports, expected',
    [('2525,', ['2525']), ('2525,,', ['2525']), ('2525, 3535,', ['2525', '3535'])],
)
def test_ports_drop_empty_items_at_the_end(ports, expected):
    """A comma at the end of ports is ignored like by Ruby's split."""
    assert SmtpdServer(SmtpdEvents(), ports=ports).ports == expected


def test_hosts_drop_empty_items_at_the_end():
    """A comma at the end of hosts is ignored like by Ruby's split."""
    assert SmtpdServer(SmtpdEvents(), hosts='127.0.0.1,').hosts == ['127.0.0.1']


@pytest.mark.parametrize(
    'ports, message',
    [(',2525', 'Do not use empty value'), ('2525,,3535', 'Do not use empty value'), ('', 'Missing port'), (',', 'Missing port')],
)
def test_ports_with_an_empty_item_are_refused(ports, message):
    """An empty item before the end or no port at all stops the setup."""
    with pytest.raises(ValueError, match=message):
        SmtpdServer(SmtpdEvents(), ports=ports)


@pytest.mark.parametrize(
    'hosts, message',
    [(',127.0.0.1', 'Detected an empty identifier'), (',', 'No hosts defined')],
)
def test_hosts_with_an_empty_item_are_refused(hosts, message):
    """An empty host before the end or no host at all stops the setup."""
    with pytest.raises(ValueError, match=message):
        SmtpdServer(SmtpdEvents(), hosts=hosts)


def test_io_buffer_max_size_none_is_the_default():
    """io_buffer_max_size None takes the default like Ruby's nil."""
    server = SmtpdServer(SmtpdEvents(), {'io_buffer_max_size': None})
    assert server.io_buffer_max_size == DEFAULT_IO_BUFFER_MAX_SIZE


@pytest.mark.parametrize('value', [0, -1, '1024', 1.5, True])
def test_io_buffer_max_size_must_be_a_positive_integer(value):
    """io_buffer_max_size refuses anything but a positive integer."""
    with pytest.raises(ValueError, match='io_buffer_max_size'):
        SmtpdServer(SmtpdEvents(), {'io_buffer_max_size': value})


def test_a_host_name_resolves_to_its_addresses():
    """A host name resolves by DNS to its addresses, each once."""
    ips = _resolve_host('localhost')
    assert '127.0.0.1' in ips
    assert len(ips) == len(set(ips))


def test_wildcard_names_survive_a_failing_lookup_of_the_local_host(monkeypatch):
    """A failing lookup of the local host keeps the other certificate names."""

    def fail(*args, **kwargs):
        raise OSError('no lookup')

    monkeypatch.setattr(socket, 'getaddrinfo', fail)
    cn, sans = _tls_cert_names(None, None, ['*'], [('0.0.0.0', 2525)])
    assert cn == 'localhost.local'
    assert {'localhost', '127.0.0.1', '::1'} <= set(sans)


def test_reverse_lookup_gives_none_without_an_ip_or_a_name(monkeypatch):
    """The reverse lookup gives None for an empty ip and an ip without name."""
    srv = SmtpdServer(SmtpdEvents())

    def unknown(ip):
        raise socket.herror('unknown host')

    monkeypatch.setattr(socket, 'gethostbyaddr', unknown)
    assert asyncio.run(srv._reverse_lookup('')) is None
    assert asyncio.run(srv._reverse_lookup('192.0.2.1')) is None
