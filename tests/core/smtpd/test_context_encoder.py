"""
JSON of a live ```ctx``` with ```SmtpdContextEncoder```.

A real dialog over smtplib serializes its ```ctx``` in
```on_message_data_event```, the way a handler logs a message or enqueues a
job: the raw body (```data```) and the spooler stay out of the JSON, the
envelope and the facts of the connection and the message are in. The
```options``` of the service reach only the handler, never the ```ctx```.
"""

import json

from tokeo.core.smtpd import SmtpdContextEncoder
from tests.core.smtpd.lib.smtpd_tests_capture_events_helper import CaptureSmtpdEvents
from tests.core.smtpd.lib.smtpd_tests_helper import send_mail, run_server_client

BODY = 'Subject: encoder\r\n\r\nTHE-BODY-MUST-NOT-REACH-THE-JSON\r\n'


class JsonEvents(CaptureSmtpdEvents):
    """Serializes the ctx of every received message, with its envelope."""

    def __init__(self, options=None):
        super().__init__(options=options)
        self.json = []
        self.envelopes = []

    def on_message_data_event(self, ctx):
        self.json.append(json.dumps(ctx, default=SmtpdContextEncoder().encode))
        self.envelopes.append((ctx.envelope.mail_from, list(ctx.envelope.rcpt_tos)))


def test_context_encoder_keeps_the_body_out_of_the_json():
    """The JSON of a live ctx holds envelope and message facts, not the body."""
    events = JsonEvents()
    run_server_client(events, {'do_dns_reverse_lookup': False}, lambda port: send_mail(port, 'a@local', 'b@local', BODY))
    assert len(events.json) == 1
    assert 'THE-BODY-MUST-NOT-REACH-THE-JSON' not in events.json[0]
    ctx = json.loads(events.json[0])
    # the message keeps its facts, only the body and the spooler are dropped
    assert 'data' not in ctx['message'] and 'spooler' not in ctx['message']
    assert ctx['message']['bytesize'] > 0
    # datetimes come as strings, the envelope as it was received
    assert isinstance(ctx['message']['delivered'], str)
    assert (ctx['envelope']['mail_from'], ctx['envelope']['rcpt_tos']) == events.envelopes[0]
    assert ctx['server']['remote_ip'] == '127.0.0.1'


def test_context_encoder_has_no_options_of_the_service():
    """The options of the service stay at the handler, the ctx JSON has none."""
    events = JsonEvents(options={'api_key': 'THE-OPTIONS-MUST-NOT-REACH-THE-JSON'})
    run_server_client(events, {'do_dns_reverse_lookup': False}, lambda port: send_mail(port, 'a@local', 'b@local', BODY))
    assert 'THE-OPTIONS-MUST-NOT-REACH-THE-JSON' not in events.json[0]
    assert set(json.loads(events.json[0])) == {'id', 'server', 'envelope', 'message'}
    assert events.options == {'api_key': 'THE-OPTIONS-MUST-NOT-REACH-THE-JSON'}
