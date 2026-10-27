"""
Tests for the results of the handler events that the server takes over.

```on_process_line_unknown_event``` answers an unknown line by a returned
single reply line with its code, by 500 on None or False and by 451 on any
other result or a foreign exception. A ```helo_response``` or
```local_response``` of None is an empty text like Ruby's ```to_s```.
```on_proxy_event``` updates the fields of ```ctx.server.proxy``` by a
returned dict (also ```{}```), keeps them on None and aborts by 421 otherwise.
"""

import pytest

from tokeo.core.smtpd.server import SmtpdServer, SmtpdSession
from tokeo.core.smtpd.events import SmtpdEvents
from tokeo.core.smtpd.exc import Smtpd421Exception
from tests.core.smtpd.lib.smtpd_tests_helper import dialog, run_line


class UnknownEvents(SmtpdEvents):
    """Answers every unknown line by the given result or exception."""

    def __init__(self, result):
        super().__init__()
        self.result = result

    def on_process_line_unknown_event(self, ctx, line):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize(
    'result, expected',
    [
        (None, '500'),
        (False, '500'),
        ('250 OK', '250 OK'),
        ('502 not here', '502 not here'),
        ('250', '250'),
        (True, '451'),
        ('hallo', '451'),
        ('250 OK\r\n250 OK', '451'),
        ('', '451'),
        (250, '451'),
        (RuntimeError('broken'), '451'),
    ],
    ids=['none', 'false', 'reply', 'other-code', 'code-only', 'true', 'no-code', 'two-lines', 'empty', 'int', 'error'],
)
def test_unknown_event_result(result, expected):
    """A reply line answers, None/False 500, else 451, the dialog goes on."""
    _, r = dialog(UnknownEvents(result), {}, [b'HELO a\r\n', b'XYZZY\r\n', b'NOOP\r\n'])
    assert r[1][0].startswith(expected)
    assert r[2] == ['250 OK']


class NoResponseEvents(SmtpdEvents):
    """Sets helo_response and local_response to None on connect."""

    def on_connect_event(self, ctx):
        super().on_connect_event(ctx)
        ctx.server.local_response = None
        ctx.server.helo_response = None


def test_none_responses_are_empty_texts():
    """A None helo_response or local_response is answered as an empty text."""
    greeting, r = dialog(NoResponseEvents(), {}, [b'HELO a\r\n', b'RSET\r\n', b'QUIT\r\n'])
    assert greeting == ['220 ']
    assert r[0] == ['250 OK']
    greeting, r = dialog(NoResponseEvents(), {}, [b'EHLO a\r\n'])
    assert r[0] == ['250-', '250 OK']


class ProxyEvents(SmtpdEvents):
    """Returns the given result from on_proxy_event."""

    def __init__(self, result):
        super().__init__()
        self.result = result

    def on_proxy_event(self, ctx, proxy_data):
        return self.result


def _proxy(result):
    smtpd = SmtpdServer(ProxyEvents(result), {'proxy_extension': True})
    session = SmtpdSession()
    smtpd.process_reset_session(session, connection_initialize=True)
    session.ctx.server.local_response = 'welcome'
    reply = run_line(smtpd, session, 'PROXY TCP4 1.1.1.1 2.2.2.2 1111 2222')
    return reply, session.ctx.server.proxy


PARSED = {
    'proto': 'TCP4',
    'source_ip': '1.1.1.1',
    'source_host': '1.1.1.1',
    'source_port': 1111,
    'dest_ip': '2.2.2.2',
    'dest_host': '2.2.2.2',
    'dest_port': 2222,
}


@pytest.mark.parametrize(
    'result, expected',
    [
        (None, PARSED),
        ({}, PARSED),
        ({'source_host': 'client.example.org'}, dict(PARSED, source_host='client.example.org')),
        ({'tenant': 'a'}, dict(PARSED, tenant='a')),
    ],
    ids=['none', 'empty', 'one-field', 'new-key'],
)
def test_proxy_event_updates_the_fields(result, expected):
    """A returned dict updates only its fields, None and {} keep the parsed data."""
    reply, proxy = _proxy(result)
    assert reply == '220 welcome'
    assert proxy == expected


@pytest.mark.parametrize('result', [True, 'x', ['a']], ids=['true', 'str', 'list'])
def test_proxy_event_with_another_result_aborts(result):
    """Any other result of on_proxy_event aborts the connection by 421."""
    with pytest.raises(Smtpd421Exception, match='on_proxy_event must not return'):
        _proxy(result)
