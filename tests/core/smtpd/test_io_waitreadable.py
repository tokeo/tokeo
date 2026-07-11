"""
Ported from midi-smtp-server test/integration/io_waitreadable_test.rb.

midi polls the socket non-blocking and sleeps ```io_waitreadable_sleep``` between
empty reads; the test measures that a larger sleep makes delivery slower. This
polling does not exist on asyncio -- ```await readuntil``` suspends until data
arrives, so ```io_waitreadable_sleep``` has no functional effect. The value is
still exposed for API parity; the timing behaviour is therefore not applicable.
"""

from tokeo.core.smtpd.server import SmtpdServer, DEFAULT_IO_WAITREADABLE_SLEEP
from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents


def test_io_waitreadable_sleep_is_exposed():
    # API parity: the setting exists and is configurable, default 0.1
    assert SmtpdServer(CaptureSmtpdEvents()).io_waitreadable_sleep == DEFAULT_IO_WAITREADABLE_SLEEP
    assert SmtpdServer(CaptureSmtpdEvents(), settings={'io_waitreadable_sleep': 0.5}).io_waitreadable_sleep == 0.5
