"""
Ported from the reference suite's test/specs/logging_test.rb.

Checks the exposed ```logger``` (ForwardingLogger) routes every level to the
handler's on_logging_event with the matching Severity and a nil context, and
that on_logging_event passes context and error through.
"""

from tokeo.core.smtpd.server import SmtpdServer
from tokeo.core.smtpd.events import SmtpdEvents, SmtpdEvent
from tokeo.core.smtpd.logger import Severity


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
    smtpd, events = _server()
    smtpd.logger.info('A simple info message.')
    _expect(events, Severity.INFO, 'A simple info message.')


def test_log_warn():
    smtpd, events = _server()
    smtpd.logger.warn('A simple warn message.')
    _expect(events, Severity.WARN, 'A simple warn message.')


def test_log_error():
    smtpd, events = _server()
    smtpd.logger.error('A simple error message.')
    _expect(events, Severity.ERROR, 'A simple error message.')


def test_log_fatal():
    smtpd, events = _server()
    smtpd.logger.fatal('A simple fatal message.')
    _expect(events, Severity.FATAL, 'A simple fatal message.')


def test_log_debug():
    smtpd, events = _server()
    smtpd.logger.debug('A simple debug message.')
    _expect(events, Severity.DEBUG, 'A simple debug message.')


def test_on_logging_event_with_context_and_err():
    _, events = _server()
    events.on_logging_event({'ctx': True}, Severity.FATAL, 'A simple fatal message.', err={'err': 1})
    assert events.log_ctx['ctx'] is True
    assert events.log_severity is Severity.FATAL
    assert events.log_msg == 'A simple fatal message.'
    assert events.log_err['err'] == 1


class SilentEvents(SmtpdEvents):
    """A handler that does not override on_logging_event (logging inactive)."""


def test_logging_dispatched_when_handler_overrides():
    # LoggingEvents overrides on_logging_event -> a dispatch entry exists
    assert SmtpdEvent.ON_LOGGING_EVENT in SmtpdServer(LoggingEvents()).emit_events


def test_logging_not_dispatched_when_handler_does_not_override():
    # the base on_logging_event is a no-op -> no dispatch entry
    assert SmtpdEvent.ON_LOGGING_EVENT not in SmtpdServer(SilentEvents()).emit_events


def test_undispatched_logging_is_not_forwarded(monkeypatch):
    # without a dispatch entry, the logger adapter short-circuits and never
    # reaches the handler, so no message is built or forwarded
    seen = []
    monkeypatch.setattr(SmtpdEvents, 'on_logging_event', lambda *a, **k: seen.append(a), raising=True)
    smtpd = SmtpdServer(SilentEvents())
    assert SmtpdEvent.ON_LOGGING_EVENT not in smtpd.emit_events
    smtpd.logger.debug('should be dropped')
    smtpd._log_bridge(None, Severity.DEBUG, 'also dropped')
    assert seen == []
