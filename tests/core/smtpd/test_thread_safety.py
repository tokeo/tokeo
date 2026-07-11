"""
Ported from the reference suite's test/stress/thread_safety_test.rb.

Runs many simultaneous authenticated deliveries and verifies the per-connection
context is never cross-contaminated: for each connection the envelope sender must
equal ```<authentication_id>``` and the first recipient. With one session/ctx per
connection task, the fail counter must stay zero.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from tokeo.core.smtpd.server import SmtpdServer
from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_helpers import read_message, send_mail


class ThreadSafetyEvents(CaptureSmtpdEvents):
    """Flags any connection whose ctx is not internally consistent."""

    def on_message_data_event(self, ctx):
        super().on_message_data_event(ctx)
        if (ctx.envelope.mail_from != f'<{ctx.server.authentication_id}>'
                or ctx.envelope.mail_from != ctx.envelope.rcpt_tos[0]):
            self.ev_fail_counter += 1


SETTINGS = {
    'max_processings': 50,
    'do_dns_reverse_lookup': False,
    'auth_mode': 'AUTH_OPTIONAL',
    'encrypt_mode': 'TLS_OPTIONAL',
    'tls_cert_cn': '127.0.0.1',
    'tls_cert_san': ['127.0.0.1'],
    'pipelining_extension': False,
    'internationalization_extensions': True,
}


def test_thread_safety_with_multiple_connections():
    ev = ThreadSafetyEvents()
    mail = read_message('simple_mail.msg')
    count = 100

    async def go():
        srv = SmtpdServer(ev, settings=SETTINGS)
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
        loop = asyncio.get_event_loop()

        def one(index):
            email = f'administrator_{index}@local.local'
            send_mail(port, email, email, mail, authentication_id=email, password='password', auth_type='plain')

        try:
            with ThreadPoolExecutor(max_workers=count) as pool:
                await asyncio.gather(*(loop.run_in_executor(pool, one, i) for i in range(count)))
            await asyncio.sleep(0.3)
        finally:
            await srv.stop(wait_seconds_before_close=1.0)

    asyncio.run(go())
    assert ev.ev_fail_counter == 0
