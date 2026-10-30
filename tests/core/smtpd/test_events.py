"""
Tests for tokeo.core.smtpd.events, the handler contract.

The two guards of ```@threaded``` fire at import, before any service starts:
on an ```async def``` and on the hot path ```on_message_data_receiving_event```.
The helpers ```authenticated``` and ```encrypted``` tell the state of a
connection from its context.
"""

import pytest

from tokeo.core.smtpd.context import SmtpdContext
from tokeo.core.smtpd.events import SmtpdEvents, threaded
from tokeo.core.smtpd.exc import SmtpdContractError
from tokeo.core.utils.date import utc_now


def test_threaded_on_an_async_event_is_refused():
    """@threaded on an async def raises SmtpdContractError at decoration."""
    with pytest.raises(SmtpdContractError, match='meaningless'):

        @threaded
        async def on_message_data_event(self, ctx):
            pass


def test_threaded_on_the_receiving_event_is_refused():
    """@threaded on the receiving event raises SmtpdContractError at once."""
    with pytest.raises(SmtpdContractError, match='must not be @threaded'):

        class Hot(SmtpdEvents):

            @threaded
            def on_message_data_receiving_event(self, ctx):
                pass


def test_authenticated_and_encrypted_follow_the_context():
    """authenticated and encrypted are False until their time is set."""
    ctx = SmtpdContext(id='x')
    assert SmtpdEvents.authenticated(ctx) is False
    assert SmtpdEvents.encrypted(ctx) is False
    ctx.server.authenticated = utc_now()
    ctx.server.encrypted = utc_now()
    assert SmtpdEvents.authenticated(ctx) is True
    assert SmtpdEvents.encrypted(ctx) is True


@pytest.mark.parametrize(
    'name, args, expected',
    [
        ('on_proxy_event', ({},), None),
        ('on_helo_event', ('me',), None),
        ('on_mail_from_event', ('<a@x>',), True),
        ('on_rcpt_to_event', ('<b@y>',), True),
        ('on_message_data_start_event', (), None),
        ('on_message_data_receiving_event', (), None),
        ('on_message_data_headers_event', (), None),
        ('on_message_data_event', (), None),
        ('on_vrfy_event', ('postmaster',), None),
        ('on_expn_event', ('team',), None),
        ('on_help_event', ('',), None),
        ('on_process_line_unknown_event', ('FOO',), None),
    ],
)
def test_the_defaults_accept_and_leave_the_answer_to_the_server(name, args, expected):
    """The defaults accept every address and leave other answers to the server."""
    assert getattr(SmtpdEvents(), name)(SmtpdContext(id='x'), *args) is expected
