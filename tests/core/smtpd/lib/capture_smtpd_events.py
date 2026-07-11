"""
A recording ```SmtpdEvents``` handler for the smtpd core-lib tests.

```CaptureSmtpdEvents``` is the shared handler used across the ported tests: it
captures the auth and message-data values into ```ev_*``` attributes so a test
can inspect what the server passed to the handler. It also implements the test
authentication used throughout (grants 'supervisor' to administrator/password,
denies everything else with 535). It is the counterpart of midi-smtp-server's
```MidiSmtpServerTest``` handler.
"""

from tokeo.core.smtpd.events import SmtpdEvents
from tokeo.core.smtpd.exc import Smtpd535Exception


class CaptureSmtpdEvents(SmtpdEvents):
    """
    Captures the auth and message-data values into ```ev_*``` attributes.

    ### Notes

    : ```on_auth_event``` grants 'supervisor' for administrator/password and
        denies everything else with ```Smtpd535Exception```
    : ```on_message_data_event``` stores the delivered message (bytes), its
        delivered timestamp and its byte size

    """

    def __init__(self, options=None):
        super().__init__(options)
        self.ev_fail_counter = 0
        self.ev_auth_authentication_id = None
        self.ev_auth_authentication = None
        self.ev_auth_authorization_id = None
        self.ev_message_data = None
        self.ev_message_delivered = None
        self.ev_message_bytesize = None

    def on_auth_event(self, ctx, authorization_id, authentication_id, authentication):
        # save local event data
        self.ev_auth_authentication_id = authentication_id
        self.ev_auth_authentication = authentication
        # return role when authenticated
        if authorization_id == '' and authentication_id.startswith('administrator') and authentication == 'password':
            self.ev_auth_authorization_id = 'supervisor'
            return self.ev_auth_authorization_id
        # otherwise exit with authentication exception
        raise Smtpd535Exception

    def on_message_data_event(self, ctx):
        # save local event data (data byte-exact as bytes)
        self.ev_message_data = bytes(ctx.message.data)
        self.ev_message_delivered = ctx.message.delivered
        self.ev_message_bytesize = ctx.message.bytesize
