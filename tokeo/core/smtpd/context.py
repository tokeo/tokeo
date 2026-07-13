"""
Tokeo SMTPD Context Module.

The per-connection ```ctx``` handed to every handler event -- a one-to-one
translation of the ```session[:ctx]``` hash (```:server```,
```:envelope```, ```:message```) into three dataclasses with attribute access.
The server fills each value directly at the point it becomes known, exactly as
the Ruby code assigns into the hash.

### Notes

: Conventions kept: string fields default to ```''```; ```proxy``` and
    ```message.spooler``` are ```None```; ```received```/```delivered```/
    ```bytesize``` start at ```-1```; ```encrypted```/```authenticated```
    are a single field each -- empty while off, a UTC timestamp once on;
    ```headers``` flips from ```False``` to ```True```; ```data``` is the
    raw message and is appended to directly
: Python keyword clash: the Ruby ```ctx[:envelope][:from]``` / ```[:to]``` are
    ```ctx.envelope.mail_from``` / ```ctx.envelope.rcpt_tos``` (```from``` and
    ```to``` are reserved words); every other name is unchanged. The body is
    ```bytes``` (not str) to stay byte-exact for signatures like DKIM.

"""

import os
import tempfile
from dataclasses import dataclass, field

from tokeo.core.utils.json import TokeoJsonEncoder


@dataclass
class ServerCtx:
    """
    Connection and server facts (```ctx[:server]```).

    """

    #: Reverse-resolved local hostname (or the local IP when DNS lookup is off)
    local_host: str = ''
    #: Local IP the connection was accepted on (which listener)
    local_ip: str = ''
    #: Local port the connection was accepted on
    local_port: object = ''
    #: Writable welcome banner text
    local_response: str = ''
    #: Reverse-resolved client hostname (or the client IP when DNS lookup is off)
    remote_host: str = ''
    #: Client IP
    remote_ip: str = ''
    #: Client port
    remote_port: object = ''
    #: PROXY header data as a dict, or None
    proxy: dict = None
    #: The HELO/EHLO name the client gave
    helo: str = ''
    #: Writable EHLO/HELO greeting text
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
    Envelope facts (```ctx[:envelope]```); reset per transaction.

    ### Notes

    : ```mail_from```/```rcpt_tos``` are the Ruby ```from```/```to```;
        ```mail_from``` is ```''``` when empty, ```rcpt_tos``` accumulates
        address by address

    """

    #: The MAIL FROM address as accepted; '' when empty
    mail_from: str = ''
    #: Accepted RCPT TO addresses
    rcpt_tos: list = field(default_factory=list)
    #: encoding_body: '7bit'/'8bitmime' from BODY=, else ''
    encoding_body: str = ''
    #: encoding_utf8: 'utf8' when SMTPUTF8 was used, else ''
    encoding_utf8: str = ''


class MessageSpooler:
    """
    Optional file spooling for one incoming message.

    Owns the spool temp file while the message streams to disk: the file
    descriptor, the writable handle, the file path and the line ending of
    the last written chunk (used for the final chomp).

    """

    __slots__ = ('fd', 'file', 'path', 'last_line_break', 'debug')

    def __init__(self, dir=None, prefix=None, debug=False):
        # create spool temp file for dir + prefix
        self.fd, self.path = tempfile.mkstemp(dir=dir, prefix=prefix, suffix='.eml')
        # open file handle to write
        self.file = os.fdopen(self.fd, 'wb')
        # reset last line_break
        self.last_line_break = b''
        # safe flag for debugging control
        self.debug = debug

    def chomp(self, cur_bytesize):
        """
        Remove the last written line break from the file.

        ### Returns

        - **int**: The file size after the chomp (```cur_bytesize``` when
            the file is already closed)

        """
        # test for open file
        if self.file:
            _bytesize = self.file.tell()
            if self.last_line_break:
                _bytesize -= len(self.last_line_break)
                self.file.truncate(_bytesize)
                self.last_line_break = b''
            return _bytesize
        # without the file, return the given value
        return cur_bytesize

    def close(self):
        """Flush and close the file handle (safe to call repeatedly)."""
        # test for open file
        if self.file:
            self.file.flush()
            self.file.close()
            self.fd = None
            self.file = None

    def unlink(self):
        """Close the file and delete it from disk (kept in debug mode)."""
        self.close()
        if not self.debug and self.path:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            self.path = None

    def keep(self, path=None):
        """
        Release the file from the spooler and keep it on disk.

        ### Args

        - **path** (str, optional): Move the file there (```os.replace```,
            atomic on the same filesystem); without it the file stays in
            place under its spool name

        ### Returns

        - **str**: The final file path, or None when already released

        ### Notes

        : ```keep``` does not flush. Called before the message completed it
            only relocates the still growing file -- the server keeps
            writing through the open handle and flushes and closes it as
            usual, so the content is complete only after
            ```on_message_data_event```

        """
        if self.path:
            # only keep or move?
            if path is not None:
                # rename with new path
                os.replace(self.path, path)
            else:
                # cache value for return
                path = self.path
            # do not touch the file anymore
            self.path = None
            # leave a result where to get the file
            return path
        else:
            return None


@dataclass
class MessageCtx:
    """
    Message facts (```ctx[:message]```); reset per transaction.

    """

    #: When body reception started (UTC); initialized with -1
    received: object = -1
    #: When body reception completed (UTC); initialized with -1
    delivered: object = -1
    #: Running total size of the message in bytes while receiving (final
    #: after the closing dot); -1 until the first DATA line
    bytesize: int = -1
    #: The active ```MessageSpooler``` while/after spooling, else None
    spooler: MessageSpooler = None
    #: headers marker: False while inside the headers, True once past them
    headers: bool = False
    #: Line-break sequence recorded for the message
    crlf: bytes = b'\r\n'
    #: The raw message octets; appended to directly
    data: bytearray = field(default_factory=bytearray, repr=False)

    def chomp(self):
        """
        Remove one trailing line break and settle the final ```bytesize```.

        With a spooler the chomp happens on the spool file (closed after);
        otherwise on ```data``` -- exactly one CRLF, LF or CR, never more.

        """
        if self.spooler:
            self.bytesize = self.spooler.chomp(self.bytesize)
            self.spooler.close()
        else:
            # not rstrip: chomp removes exactly one line break, rstrip would eat all
            if self.data.endswith(b'\r\n'):
                del self.data[-2:]
            elif self.data.endswith(b'\n') or self.data.endswith(b'\r'):
                del self.data[-1:]
            self.bytesize = len(self.data)

    def finalize(self):
        """Dispose an attached spooler (deletes unless kept or debug)."""
        if self.spooler:
            self.spooler.unlink()
            self.spooler = None


@dataclass
class SmtpdContext:
    """
    The per-connection context handed to every handler event.

    ### Notes

    : One instance per connection; ```server``` lives for the whole connection,
        ```envelope```/```message``` are replaced on RSET and after each
        delivery
    : ```id``` uniquely identifies the connection in logs and dumps and is
        write-once -- a second assignment raises ```AttributeError```

    """

    #: Unique session id (hex string), set once when the context is created
    id: str = ''
    server: ServerCtx = field(default_factory=ServerCtx)
    envelope: EnvelopeCtx = field(default_factory=EnvelopeCtx)
    message: MessageCtx = field(default_factory=MessageCtx)
    options: dict = field(default_factory=dict)

    def __setattr__(self, name, value):
        """Guard the write-once session ```id``` against a second assignment."""
        if name == 'id' and getattr(self, 'id', ''):
            raise AttributeError('SmtpdContext.id is read-only')
        super().__setattr__(name, value)

    def finalize(self):
        """Finish interrupted per-message resources (the spooler)."""
        if self.message:
            self.message.finalize()


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
            return {key: value for key, value in obj.__dict__.items() if key not in ('data', 'spooler')}
        return super().encode(obj)
