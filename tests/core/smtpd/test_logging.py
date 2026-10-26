"""
Ported from the reference suite's test/specs/logging_test.rb.

Checks the exposed ```logger``` (ForwardingLogger) routes every level to the
handler's on_logging_event with the matching Severity and a nil context, and
that on_logging_event passes context and error through. The default
on_logging_event writes to the server logger: the given one or an own stderr
logger with logger_severity. The log lines of the server and the default events
follow the reference: connect, disconnect, auth denial, start, command lines and
faults outside the dialog.
"""

import re
import asyncio

import pytest

from tokeo.core.smtpd.server import SmtpdServer
from tokeo.core.smtpd.tls import TlsTransport
from tokeo.core.smtpd.events import SmtpdEvents, SmtpdEvent
from tokeo.core.smtpd.logger import Severity
from tests.core.smtpd.lib.smtpd_tests_helper import dialog, auth_plain


class LoggingEvents(SmtpdEvents):
    """Records the arguments handed to on_logging_event."""

    def __init__(self, options=None):
        super().__init__(options)
        self.log_ctx = 'unset'
        self.log_severity = None
        self.log_msg = None
        self.log_err = 'unset'

    def on_logging_event(self, ctx, severity, msg, err=None):
        self.log_ctx = ctx
        self.log_severity = severity
        self.log_msg = msg
        self.log_err = err


def _server():
    events = LoggingEvents()
    return SmtpdServer(events), events


def _expect(events, severity, msg):
    assert events.log_ctx is None
    assert events.log_severity is severity
    assert events.log_msg == msg
    assert events.log_err is None


def test_log_info():
    """logger.info reaches on_logging_event with INFO and no ctx."""
    smtpd, events = _server()
    smtpd.logger.info('A simple info message.')
    _expect(events, Severity.INFO, 'A simple info message.')


def test_log_warning():
    """logger.warning reaches on_logging_event with WARNING and no ctx."""
    smtpd, events = _server()
    smtpd.logger.warning('A simple warning message.')
    _expect(events, Severity.WARNING, 'A simple warning message.')


def test_log_error():
    """logger.error reaches on_logging_event with ERROR and no ctx."""
    smtpd, events = _server()
    smtpd.logger.error('A simple error message.')
    _expect(events, Severity.ERROR, 'A simple error message.')


def test_log_critical():
    """logger.critical reaches on_logging_event with CRITICAL and no ctx."""
    smtpd, events = _server()
    smtpd.logger.critical('A simple critical message.')
    _expect(events, Severity.CRITICAL, 'A simple critical message.')


def test_log_debug():
    """logger.debug reaches on_logging_event with DEBUG and no ctx."""
    smtpd, events = _server()
    smtpd.logger.debug('A simple debug message.')
    _expect(events, Severity.DEBUG, 'A simple debug message.')


def test_on_logging_event_with_context_and_err():
    """on_logging_event passes ctx and err through."""
    _, events = _server()
    events.on_logging_event({'ctx': True}, Severity.CRITICAL, 'A simple critical message.', err={'err': 1})
    assert events.log_ctx['ctx'] is True
    assert events.log_severity is Severity.CRITICAL
    assert events.log_msg == 'A simple critical message.'
    assert events.log_err['err'] == 1


class DefaultEvents(SmtpdEvents):
    """A handler that does not override on_logging_event."""


def test_logging_dispatched_when_handler_overrides():
    """An overridden on_logging_event gets a dispatch entry."""
    assert SmtpdEvent.ON_LOGGING_EVENT in SmtpdServer(LoggingEvents()).emit_events


def test_logging_dispatched_when_handler_does_not_override():
    """The default on_logging_event gets a dispatch entry too."""
    assert SmtpdEvent.ON_LOGGING_EVENT in SmtpdServer(DefaultEvents()).emit_events


class Recorder:
    """A logger that records the calls per method."""

    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda msg: self.lines.append((level, msg))


def test_default_writes_from_warning_to_an_own_stderr_logger(capsys):
    """Without a logger the default writes from WARNING on, formatted, to stderr."""
    server = SmtpdServer(SmtpdEvents())
    server.logger.info('quiet')
    server.logger.warning('hello')
    out, err = capsys.readouterr()
    assert out == ''
    assert re.match(r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d: \[WARNING\] hello\n$', err)


def test_logger_severity_sets_the_level_of_the_own_logger(capsys):
    """logger_severity DEBUG lets the own logger write the whole dialog."""
    server = SmtpdServer(SmtpdEvents(), {'logger_severity': 'DEBUG'})
    server.logger.debug('dialog')
    assert '[DEBUG] dialog' in capsys.readouterr().err


def test_logger_severity_is_refused_with_an_own_logger():
    """A given logger keeps its level, logger_severity must not be set with it."""
    with pytest.raises(ValueError, match='must not be set with an own logger'):
        SmtpdServer(SmtpdEvents(), {'logger_severity': 'DEBUG'}, logger=Recorder())


@pytest.mark.parametrize('value', ['debug', 'WARN', 10, ''])
def test_logger_severity_is_validated(value):
    """logger_severity accepts only the Severity names."""
    with pytest.raises(ValueError, match='logger_severity'):
        SmtpdServer(SmtpdEvents(), {'logger_severity': value})


def test_given_logger_is_the_target_and_replaces_a_preset_one():
    """A given logger receives the lines, a value set on the handler is replaced."""
    events = SmtpdEvents()
    events._logger_protected = Recorder()
    target = Recorder()
    server = SmtpdServer(events, logger=target)
    server.logger.error('boom')
    assert target.lines == [('error', 'boom')]
    assert events._logger_protected is target


def test_forwarding_logger_is_refused_as_target():
    """The log input of a handler must not be the target (endless loop)."""
    with pytest.raises(ValueError, match='logger'):
        SmtpdServer(SmtpdEvents(), logger=SmtpdServer(SmtpdEvents()).logger)


def test_override_reaches_the_server_logger_by_super():
    """An override that calls super writes to the server logger as well."""

    class Own(SmtpdEvents):

        def __init__(self):
            super().__init__()
            self.seen = []

        def on_logging_event(self, ctx, severity, msg, err=None):
            self.seen.append(msg)
            super().on_logging_event(ctx, severity, msg, err)

    target = Recorder()
    events = Own()
    SmtpdServer(events, logger=target).logger.info('both\r\n')
    assert events.seen == ['both\r\n']
    # the line break is chomped like by Ruby
    assert target.lines == [('info', 'both')]


def test_critical_with_error_is_followed_by_its_traceback():
    """A CRITICAL line with an error writes the traceback lines after it."""
    target = Recorder()
    events = SmtpdEvents()
    SmtpdServer(events, logger=target)
    try:
        raise RuntimeError('broken')
    except RuntimeError as err:
        events.on_logging_event(None, Severity.CRITICAL, 'fatal', err)
    assert target.lines[0] == ('critical', 'fatal')
    assert target.lines[1] == ('critical', 'Traceback (most recent call last):')
    assert target.lines[-1] == ('critical', 'RuntimeError: broken')


class LinesEvents(SmtpdEvents):
    """Collects every log line with its ctx, the other events keep their default."""

    def __init__(self, options=None):
        super().__init__(options)
        self.lines = []

    def on_logging_event(self, ctx, severity, msg, err=None):
        self.lines.append((ctx, severity, msg, err))

    def messages(self):
        return [msg for _, _, msg, _ in self.lines]


def test_forwarding_logger_passes_a_ctx():
    """self.log.<level>(msg, ctx=ctx) hands the ctx to on_logging_event."""
    events = LoggingEvents()
    SmtpdServer(events).logger.info('with ctx', ctx='CTX')
    assert (events.log_ctx, events.log_severity, events.log_msg) == ('CTX', Severity.INFO, 'with ctx')


def test_default_connect_and_disconnect_are_logged_with_ctx():
    """The default connect and disconnect events log their line with the ctx."""
    events = LinesEvents()
    dialog(events, {}, [b'QUIT\r\n'])
    connect = [line for line in events.lines if line[2].startswith('Client connect from 127.0.0.1:')]
    disconnect = [line for line in events.lines if line[2].startswith('Client disconnect from 127.0.0.1:')]
    assert len(connect) == len(disconnect) == 1
    assert connect[0][0] is not None and connect[0][0] is disconnect[0][0]


def test_connect_override_without_super_logs_no_line():
    """An on_connect_event override without super() drops the default line."""

    class Own(LinesEvents):

        def on_connect_event(self, ctx):
            pass

    events = Own()
    dialog(events, {}, [b'QUIT\r\n'])
    assert not any(msg.startswith('Client connect') for msg in events.messages())
    assert any(msg.startswith('Client disconnect') for msg in events.messages())


def test_default_auth_deny_logs_without_the_password():
    """The default on_auth_event logs the denial without the password."""
    events = LinesEvents()
    dialog(events, {'auth_mode': 'AUTH_OPTIONAL'}, [b'EHLO a\r\n', b'AUTH PLAIN ' + auth_plain('', 'user', 'pa55word').encode() + b'\r\n'])
    deny = [msg for msg in events.messages() if msg.startswith('Deny access from')]
    assert deny and deny[0].endswith('for user')
    assert not any('pa55word' in msg for msg in events.messages())


@pytest.mark.parametrize(
    'crlf_mode, line, logged',
    [
        ('CRLF_ENSURE', b'NOOP \xc3\xa4\r\n', '<<< NOOP ä'),
        ('CRLF_LEAVE', b'NOOP a\rb\r\n', '<<< NOOP a[\\r]b'),
        ('CRLF_ENSURE', b'NOOP \xff\r\n', '<<< NOOP \\xff'),
    ],
)
def test_command_lines_are_logged_as_text(crlf_mode, line, logged):
    """A command line is logged as text, a CR as [\\r] and bad bytes escaped."""
    events = LinesEvents()
    dialog(events, {'crlf_mode': crlf_mode}, [b'EHLO a\r\n', line])
    assert logged in events.messages()


@pytest.mark.parametrize('host, shown, bound', [('127.0.0.1', '127.0.0.1', '127.0.0.1'), ('::1', '[::1]', '[::1]')])
def test_start_logs_every_listener_and_its_bound_address(host, shown, bound):
    """start() logs the listener as given and each bound address, both at INFO."""
    events = LinesEvents()

    async def go():
        server = SmtpdServer(events)
        await server.start([{'host': host, 'port': 0}])
        port = server._servers[0].sockets[0].getsockname()[1]
        await server.stop(wait_seconds_before_close=0)
        return port

    try:
        port = asyncio.run(go())
    except OSError:
        pytest.skip(f'{host} is not available here')
    assert (None, Severity.INFO, f'Starting service on {shown}:(auto)', None) in events.lines
    assert (None, Severity.INFO, f'Service listening on {bound}:{port}', None) in events.lines


def test_disconnect_fault_is_logged_critical_and_closes_the_connection():
    """A fault of on_disconnect_event is logged CRITICAL, the connection closes."""

    class Broken(LinesEvents):

        def on_disconnect_event(self, ctx):
            raise RuntimeError('cleanup failed')

    events = Broken()
    dialog(events, {}, [b'QUIT\r\n'])
    critical = [line for line in events.lines if line[1] is Severity.CRITICAL]
    assert critical[0][2] == 'cleanup failed (RuntimeError)'
    assert isinstance(critical[0][3], RuntimeError)


def test_generated_test_certificate_is_logged_as_pem():
    """The self-signed test certificate is logged at DEBUG as PEM."""
    lines = []
    TlsTransport(cert_cn='localhost', logger=lambda severity, msg: lines.append((severity, msg)))
    pem = [msg for severity, msg in lines if msg.startswith('SSL: generated test certificate\n')]
    assert pem and '-----BEGIN CERTIFICATE-----' in pem[0]
    assert all(severity is Severity.DEBUG for severity, _ in lines)
