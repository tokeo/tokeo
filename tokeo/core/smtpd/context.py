"""
Tokeo SMTPD Context Module.

The per-connection ```ctx``` handed to every handler event -- a one-to-one
translation of midi-smtp-server's ```session[:ctx]``` hash (```:server```,
```:envelope```, ```:message```) into three dataclasses with attribute access.
The server fills each value directly at the point it becomes known, exactly as
midi assigns into the hash.

### Notes

: midi conventions kept: string fields default to ```''```; ```proxy``` is
    ```None```; ```received```/```delivered```/```bytesize``` start at ```-1```;
    ```encrypted```/```authenticated``` are a single field each -- empty while
    off, a UTC timestamp once on; ```headers``` flips from ```''``` to
    ```'true'```; ```data``` is the raw message and is appended to directly
    (```ctx.message.data += line + line_break``` like midi's ```data <<```).
: Python keyword clash: midi's ```ctx[:envelope][:from]``` / ```[:to]``` are
    ```ctx.envelope.mail_from``` / ```ctx.envelope.rcpt_tos``` (```from``` and
    ```to``` are reserved words); every other name matches midi. The body is
    ```bytes``` (not str) to stay byte-exact for signatures like DKIM.

"""

from dataclasses import dataclass, field

from tokeo.core.utils.json import TokeoJsonEncoder


@dataclass
class ServerCtx:
    """
    Connection and server facts -- midi ```ctx[:server]```.

    """

    #: Reverse-resolved local hostname (or the local IP when DNS lookup is off)
    local_host: str = ''
    #: Local IP the connection was accepted on (which listener)
    local_ip: str = ''
    #: Local port the connection was accepted on
    local_port: object = ''
    #: Writable welcome banner text (midi: "<local_host> says welcome!")
    local_response: str = ''
    #: Reverse-resolved client hostname (or the client IP when DNS lookup is off)
    remote_host: str = ''
    #: Client IP
    remote_ip: str = ''
    #: Client port
    remote_port: object = ''
    #: PROXY header data as midi's dict, or None
    proxy: dict = None
    #: The HELO/EHLO name the client gave
    helo: str = ''
    #: Writable EHLO/HELO greeting text (midi: "<local_host> at your service!")
    helo_response: str = ''
    #: When the connection was established (UTC)
    connected: object = ''
    #: Count of exceptions seen on this connection
    exceptions: int = 0
    #: Collected error objects for logging
    errors: list = field(default_factory=list)
    #: SASL authorization id set on a successful AUTH
    authorization_id: str = ''
    #: SASL authentication id set on a successful AUTH
    authentication_id: str = ''
    #: When AUTH succeeded (UTC); empty/None while unauthenticated
    authenticated: object = ''
    #: When STARTTLS completed (UTC); empty/None while plain
    encrypted: object = ''


@dataclass
class EnvelopeCtx:
    """
    Envelope facts -- midi ```ctx[:envelope]```; reset per transaction.

    ### Notes

    : ```mail_from```/```rcpt_tos``` are midi's ```from```/```to```;
        ```mail_from``` is ```''``` when empty, ```rcpt_tos``` accumulates
        address by address

    """

    #: The MAIL FROM address as accepted (midi ```from```); '' when empty
    mail_from: str = ''
    #: Accepted RCPT TO addresses (midi ```to```)
    rcpt_tos: list = field(default_factory=list)
    #: midi encoding_body: '7bit'/'8bitmime' from BODY=, else ''
    encoding_body: str = ''
    #: midi encoding_utf8: 'utf8' when SMTPUTF8 was used, else ''
    encoding_utf8: str = ''


@dataclass
class MessageCtx:
    """
    Message facts -- midi ```ctx[:message]```; reset per transaction.

    """

    #: When body reception started (UTC); midi initializes with -1
    received: object = -1
    #: When body reception completed (UTC); midi initializes with -1
    delivered: object = -1
    #: Final message size, set when the message completes; midi -1 until then
    bytesize: int = -1
    #: midi headers marker: '' while inside the headers, 'true' once past them
    headers: str = ''
    #: Line-break sequence recorded for the message (midi crlf)
    crlf: bytes = b'\r\n'
    #: The raw message octets; appended to directly (midi ```data <<```)
    data: bytearray = field(default_factory=bytearray, repr=False)

    def chomp(self):
        """midi's ```data.chomp!```: remove one trailing CRLF, LF or CR."""
        # not rstrip: chomp removes exactly one line break, rstrip would eat all
        if self.data.endswith(b'\r\n'):
            del self.data[-2:]
        elif self.data.endswith(b'\n') or self.data.endswith(b'\r'):
            del self.data[-1:]


@dataclass
class SmtpdContext:
    """
    The per-connection context handed to every handler event.

    ### Notes

    : One instance per connection; ```server``` lives for the whole connection,
        ```envelope```/```message``` are replaced by ```reset_transaction``` on
        RSET and after each delivery (midi's process_reset_session)

    """

    server: ServerCtx = field(default_factory=ServerCtx)
    envelope: EnvelopeCtx = field(default_factory=EnvelopeCtx)
    message: MessageCtx = field(default_factory=MessageCtx)
    options: dict = field(default_factory=dict)

    def reset_transaction(self):
        """Reset envelope and message values (midi process_reset_session)."""
        self.envelope = EnvelopeCtx()
        self.message = MessageCtx()


class SmtpdContextEncoder(TokeoJsonEncoder):
    """
    JSON encoder for a live ```SmtpdContext```.

    The base ```TokeoJsonEncoder``` already unpacks dataclasses and formats
    datetimes; the only addition here is dropping the raw body (```data```)
    from ```MessageCtx``` so a potentially huge message never reaches the JSON.

    """

    def encode(self, obj):
        """Strip the raw body from MessageCtx, defer the rest to the base."""
        if isinstance(obj, MessageCtx):
            return {key: value for key, value in obj.__dict__.items() if key != 'data'}
        return super().encode(obj)
