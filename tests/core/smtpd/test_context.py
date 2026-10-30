"""
Tests for the chomp of a message in tokeo.core.smtpd.context.

The chomp at the end of DATA removes exactly one line break, CRLF, LF or CR,
from the message in memory, and from the spool file as long as it is open.
"""

import pytest

from tokeo.core.smtpd.context import MessageCtx, MessageSpooler


@pytest.mark.parametrize(
    'data, chomped',
    [
        (b'body\r\n', b'body'),
        (b'body\n', b'body'),
        (b'body\r', b'body'),
        (b'body\n\n', b'body\n'),
        (b'body', b'body'),
    ],
)
def test_chomp_removes_exactly_one_line_break(data, chomped):
    """The chomp of a message in memory removes one CRLF, LF or CR, never more."""
    message = MessageCtx(data=bytearray(data))
    message.chomp()
    assert bytes(message.data) == chomped
    assert message.bytesize == len(chomped)


def test_chomp_of_a_closed_spool_file_keeps_the_size(tmp_path):
    """Once the spool file is closed, the chomp returns the given size."""
    spooler = MessageSpooler(dir=str(tmp_path))
    spooler.file.write(b'body\r\n')
    spooler.last_line_break = b'\r\n'
    spooler.close()
    assert spooler.chomp(6) == 6
    spooler.unlink()
