"""
Tests for the whitespace_mode of the command lines.

The verb of a command is ASCII only. ```WHITESPACE_MODERATE``` (default) strips
unusual whitespace (any except SP and HTAB) and control chars at the edges as
noise, separates by any whitespace and refuses a control char inside by 501.
```WHITESPACE_STRICT``` refuses them by 501 and separates only by SP and HTAB,
```WHITESPACE_UNSAFE``` separates only by SP and HTAB and hands all other chars
to the events. Without a valid separator after the verb a command is unknown
(500); QUIT, RSET, DATA and STARTTLS answer an argument by 501, NOOP ignores
it.
"""

import asyncio

import pytest

from tokeo.core.smtpd.server import SmtpdServer, WhitespaceMode
from tokeo.core.smtpd.events import SmtpdEvents
from tests.core.smtpd.lib.smtpd_tests_helper import AuthEvents, dialog, auth_plain


class ValueEvents(SmtpdEvents):
    """Records the values handed to the HELO, MAIL and RCPT events."""

    def __init__(self, options=None):
        super().__init__(options)
        self.values = []

    def on_helo_event(self, ctx, helo):
        self.values.append(('helo', helo))

    def on_mail_from_event(self, ctx, mail_from):
        self.values.append(('mail', mail_from))
        return True

    def on_rcpt_to_event(self, ctx, rcpt_to):
        self.values.append(('rcpt', rcpt_to))
        return True


def _reply(mode, lines):
    """Run the lines, after a HELO if needed, and return the last reply code."""
    commands = [line + b'\r\n' for line in lines]
    if not lines[0].upper().startswith((b'HELO', b'EHLO')):
        commands.insert(0, b'HELO a\r\n')
    _, r = dialog(ValueEvents(), {'whitespace_mode': mode}, commands)
    return r[-1][0][:3]


MODES = ['WHITESPACE_MODERATE', 'WHITESPACE_STRICT', 'WHITESPACE_UNSAFE']


@pytest.mark.parametrize(
    'line, expected',
    [
        (b'EHLO x', ('250', '250', '250')),
        (b'ehlo\tx', ('250', '250', '250')),
        ('EHLO\xa0x'.encode(), ('250', '500', '500')),
        (b'EHLO\x0bx', ('250', '500', '500')),
        ('EHLO x\xa0'.encode(), ('250', '501', '250')),
        (b'HELO a\x00b', ('501', '501', '250')),
        (b'EHLOx', ('500', '500', '500')),
        (b'QUIT x', ('501', '501', '501')),
        (b'QUIT\x1e', ('221', '500', '500')),
        (b'QUIT \x1e', ('221', '501', '501')),
        (b'NOOP x', ('250', '250', '250')),
        (b'RSET x', ('501', '501', '501')),
        (b'DATA x', ('501', '501', '501')),
        ('RſET'.encode(), ('500', '500', '500')),
        (b'MAIL FROM:<a@x>', ('250', '250', '250')),
    ],
)
def test_replies_per_mode(line, expected):
    """Each line answers per whitespace_mode as agreed in the table of the modes."""
    assert tuple(_reply(mode, [line]) for mode in MODES) == expected


@pytest.mark.parametrize(
    'mode, line, value',
    [
        ('WHITESPACE_MODERATE', ' \xa0EHLO\xa0\xa0a\xa0b\x1f ', 'a\xa0b'),
        ('WHITESPACE_STRICT', 'EHLO\t a', 'a'),
        ('WHITESPACE_UNSAFE', 'HELO a\x00b', 'a\x00b'),
        ('WHITESPACE_UNSAFE', 'EHLO x\xa0', 'x\xa0'),
    ],
)
def test_helo_value_per_mode(mode, line, value):
    """MODERATE strips the edges and keeps the inside, UNSAFE keeps all chars."""
    events = ValueEvents()
    _, r = dialog(events, {'whitespace_mode': mode}, [line.encode() + b'\r\n'])
    assert r[0][-1].startswith('250')
    assert events.values == [('helo', value)]


@pytest.mark.parametrize(
    'mode, line, value',
    [
        ('WHITESPACE_MODERATE', 'MAIL\xa0FROM:\xa0<a@x>\xa0', '<a@x>'),
        ('WHITESPACE_MODERATE', 'MAIL FROM: <a@x>', '<a@x>'),
        ('WHITESPACE_STRICT', 'MAIL\tFROM:\t<a@x>\t', '<a@x>'),
        ('WHITESPACE_UNSAFE', 'MAIL FROM:<a\xa0@x>', '<a\xa0@x>'),
    ],
)
def test_mail_from_value_per_mode(mode, line, value):
    """The address reaches on_mail_from_event as separated by the mode."""
    events = ValueEvents()
    _, r = dialog(events, {'whitespace_mode': mode}, [b'HELO a\r\n', line.encode() + b'\r\n'])
    assert r[1][0] == '250 OK'
    assert events.values[-1] == ('mail', value)


@pytest.mark.parametrize(
    'mode, expected',
    [('WHITESPACE_MODERATE', '555'), ('WHITESPACE_STRICT', '501'), ('WHITESPACE_UNSAFE', '501')],
)
def test_mail_from_param_after_no_break_space(mode, expected):
    """MODERATE splits a parameter at a no-break space, others refuse it."""
    line = 'MAIL FROM:<a@x>\xa0SIZE=1'.encode()
    assert _reply(mode, [line]) == expected


@pytest.mark.parametrize(
    'mode, expected',
    [('WHITESPACE_MODERATE', '555'), ('WHITESPACE_STRICT', '501'), ('WHITESPACE_UNSAFE', '555')],
)
def test_rcpt_to_param_after_no_break_space(mode, expected):
    """RCPT TO answers any parameter by 555, STRICT the unusual char by 501."""
    line = 'RCPT TO:<b@y>\xa0NOTIFY=NEVER'.encode()
    assert _reply(mode, [b'MAIL FROM:<a@x>', line]) == expected


def test_helo_without_domain_and_without_handler_answers_501():
    """Without an own on_helo_event an empty domain is answered by 501."""
    _, r = dialog(SmtpdEvents(), {}, [b'HELO\r\n', b'EHLO \r\n'])
    assert r[0][0].startswith('501')
    assert r[1][0].startswith('501')


def test_helo_without_domain_is_left_to_the_handler():
    """With an own on_helo_event an empty domain is the decision of the handler."""
    events = ValueEvents()
    _, r = dialog(events, {}, [b'HELO\r\n'])
    assert r[0][0].startswith('250')
    assert events.values == [('helo', '')]


@pytest.mark.parametrize('mode', MODES)
def test_auth_with_tab_separators(mode):
    """AUTH separates the mechanism and the initial response by HTAB too."""
    response = auth_plain('', 'user', 'secret').encode()
    _, r = dialog(
        AuthEvents(), {'auth_mode': 'AUTH_OPTIONAL', 'whitespace_mode': mode}, [b'EHLO me\r\n', b'AUTH\tPLAIN\t' + response + b'\r\n']
    )
    assert r[1][0] == '235 OK'


@pytest.mark.parametrize('line', [b'AUTH', b'AUTH PLAIN a b'])
def test_auth_with_a_wrong_number_of_parts_answers_501(line):
    """AUTH takes a mechanism and an optional initial response, else 501."""
    _, r = dialog(AuthEvents(), {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO me\r\n', line + b'\r\n'])
    assert r[1][0].startswith('501')


@pytest.mark.parametrize('mode, expected', [('WHITESPACE_MODERATE', '235'), ('WHITESPACE_STRICT', '501'), ('WHITESPACE_UNSAFE', '501')])
def test_auth_response_with_a_no_break_space_at_the_edge(mode, expected):
    """MODERATE strips a no-break space around an AUTH response as noise."""
    response = auth_plain('', 'user', 'secret').encode()
    lines = [b'EHLO me\r\n', b'AUTH PLAIN\r\n', response + '\xa0'.encode() + b'\r\n']
    _, r = dialog(AuthEvents(), {'auth_mode': 'AUTH_OPTIONAL', 'whitespace_mode': mode}, lines)
    assert r[2][0][:3] == expected


def test_defaults_whitespace_mode():
    """whitespace_mode defaults to WHITESPACE_MODERATE."""
    assert SmtpdServer(SmtpdEvents()).whitespace_mode is WhitespaceMode.WHITESPACE_MODERATE


def test_unknown_whitespace_mode_is_rejected():
    """An unknown whitespace_mode names the allowed values."""
    with pytest.raises(ValueError, match='whitespace_mode must be one of'):
        SmtpdServer(SmtpdEvents(), {'whitespace_mode': 'WHITESPACE_LAX'})


def test_unsafe_logs_a_warning_on_start():
    """start() with WHITESPACE_UNSAFE logs a warning about the control chars."""
    lines = []

    class Lines(SmtpdEvents):

        def on_logging_event(self, ctx, severity, msg, err=None):
            lines.append(msg)

    async def go():
        server = SmtpdServer(Lines(), {'whitespace_mode': 'WHITESPACE_UNSAFE'})
        await server.start([{'host': '127.0.0.1', 'port': 0}])
        await server.stop(wait_seconds_before_close=0)

    asyncio.run(go())
    assert 'Whitespace of commands (whitespace_mode) WHITESPACE_UNSAFE passes control chars to the events!' in lines
