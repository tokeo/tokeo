"""
Tests for the spool option: stream every message to a temp file while
receiving, owned by ``ctx.message.spooler`` (a ``MessageSpooler``).

Contract: ``data`` carries ONLY the headers (no separator line, injected
header lines included); the file holds the complete de-dot-stuffed, chomped
message, byte-identical to the RAM mode; ``bytesize`` runs live in both
modes and equals the final file size. The file lives until
``on_message_data_event`` ends -- taken over via ``spooler.keep(path)`` /
``spooler.keep()`` -- and is kept with ``debug`` (including interrupted
files). A message without a header/body separator never gets a spooler.
"""

import asyncio
import os
import re

import pytest

from tokeo.core.smtpd.server import SmtpdServer
from tokeo.core.smtpd.events import SmtpdEvents

from tests.core.smtpd.lib.smtpd_helpers import send_mail


MESSAGE = (
    'Subject: spool test\r\n'
    'From: a@x.org\r\n'
    '\r\n'
    'line one\r\n'
    '.leading dot gets stuffed by the client\r\n'
    'last line'
)


class Capture(SmtpdEvents):
    """Records data/spooler facts at on_message_data_event time."""

    def __init__(self, options=None):
        super().__init__(options)
        self.seen = []
        self.receiving_sizes = []

    def on_message_data_receiving_event(self, ctx):
        self.receiving_sizes.append(ctx.message.bytesize)

    def on_message_data_event(self, ctx):
        entry = {'data': bytes(ctx.message.data), 'bytesize': ctx.message.bytesize, 'path': None, 'file': None}
        spooler = ctx.message.spooler
        if spooler is not None and spooler.path:
            entry['path'] = spooler.path
            with open(spooler.path, 'rb') as f:
                entry['file'] = f.read()
        self.seen.append(entry)


class InjectingCapture(Capture):
    """Injects a header line during on_message_data_headers_event."""

    def on_message_data_headers_event(self, ctx):
        ctx.message.data += b'X-inject: Y\r\n'


class KeepCapture(Capture):
    """Claims the file in place via spooler.keep()."""

    def on_message_data_event(self, ctx):
        super().on_message_data_event(ctx)
        self.seen[-1]['kept'] = ctx.message.spooler.keep()


class MoveCapture(Capture):
    """Takes the file over via spooler.keep(target)."""

    def on_message_data_event(self, ctx):
        super().on_message_data_event(ctx)
        target = ctx.message.spooler.path + '.taken'
        assert ctx.message.spooler.keep(target) == target
        self.seen[-1]['target'] = target


class EarlyMoveCapture(Capture):
    """Moves the still growing file mid-stream (first body line)."""

    def __init__(self, options=None):
        super().__init__(options)
        self.target = None

    def on_message_data_receiving_event(self, ctx):
        super().on_message_data_receiving_event(ctx)
        if self.target is None and ctx.message.headers and ctx.message.spooler:
            self.target = ctx.message.spooler.path + '.collect'
            assert ctx.message.spooler.keep(self.target) == self.target


class RenameCapture(Capture):
    """Takes the file over manually via os.rename (raw pattern)."""

    def on_message_data_event(self, ctx):
        super().on_message_data_event(ctx)
        target = ctx.message.spooler.path + '.renamed'
        os.rename(ctx.message.spooler.path, target)
        self.seen[-1]['target'] = target


def _run(events, settings, messages=(MESSAGE,), debug=False):
    async def go():
        srv = SmtpdServer(events, settings=settings, debug=debug)
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
        loop = asyncio.get_event_loop()
        try:
            for msg in messages:
                code, _ = await loop.run_in_executor(
                    None, lambda m=msg: send_mail(port, 'a@x.org', 'to@y.org', m)
                )
                assert code == 250
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(go())


# --- contract: file vs data vs bytesize ---------------------------------------


def test_file_is_byte_identical_to_ram_mode(tmp_path):
    ram = Capture()
    _run(ram, {})
    spool = Capture()
    _run(spool, {'spool': str(tmp_path) + '/'})
    assert spool.seen[0]['file'] == ram.seen[0]['data']
    assert spool.seen[0]['bytesize'] == ram.seen[0]['bytesize'] == len(ram.seen[0]['data'])


def test_data_holds_only_headers_without_separator_line(tmp_path):
    events = Capture()
    _run(events, {'spool': str(tmp_path) + '/'})
    assert events.seen[0]['data'] == b'Subject: spool test\r\nFrom: a@x.org\r\n'
    import email
    assert email.message_from_bytes(events.seen[0]['data'])['Subject'] == 'spool test'


def test_injected_headers_reach_file_and_bytesize(tmp_path):
    ram = InjectingCapture()
    _run(ram, {})
    spool = InjectingCapture()
    _run(spool, {'spool': str(tmp_path) + '/'})
    # injected header sits before the separator, file identical to RAM mode
    assert b'X-inject: Y\r\n\r\n' in spool.seen[0]['file']
    assert spool.seen[0]['file'] == ram.seen[0]['data']
    assert spool.seen[0]['bytesize'] == len(spool.seen[0]['file']) == ram.seen[0]['bytesize']


def test_bytesize_runs_live_in_both_modes(tmp_path):
    for settings in ({}, {'spool': str(tmp_path) + '/'}):
        events = Capture()
        _run(events, settings)
        sizes = events.receiving_sizes
        assert sizes == sorted(sizes) and sizes[0] > 0
        # final bytesize = last running value minus the chomped line break
        assert events.seen[0]['bytesize'] == sizes[-1] - 2


def test_message_without_separator_gets_no_spooler(tmp_path):
    ram = Capture()
    _run(ram, {}, messages=('Subject: only headers\r\nX-test: 1',))
    spool = Capture()
    _run(spool, {'spool': str(tmp_path) + '/'}, messages=('Subject: only headers\r\nX-test: 1',))
    assert spool.seen[0]['path'] is None
    assert spool.seen[0]['data'] == ram.seen[0]['data']
    assert spool.seen[0]['bytesize'] == ram.seen[0]['bytesize']
    assert os.listdir(tmp_path) == []


# --- file naming and lifecycle -------------------------------------------------


def test_file_name_pattern_and_prefix(tmp_path):
    events = Capture()
    _run(events, {'spool': str(tmp_path) + '/mx1-'}, debug=True)
    name = os.path.basename(events.seen[0]['path'])
    assert re.fullmatch(r'mx1-\d{8}-\d{6}-.+\.eml', name)
    # trailing slash = plain directory, no name prefix
    events = Capture()
    _run(events, {'spool': str(tmp_path) + '/'}, debug=True)
    assert re.fullmatch(r'\d{8}-\d{6}-.+\.eml', os.path.basename(events.seen[0]['path']))


def test_file_deleted_after_event(tmp_path):
    events = Capture()
    _run(events, {'spool': str(tmp_path) + '/'})
    assert events.seen[0]['file'] is not None      # existed during the event
    assert os.listdir(tmp_path) == []              # gone afterwards


def test_debug_keeps_delivered_files(tmp_path):
    events = Capture()
    _run(events, {'spool': str(tmp_path) + '/'}, debug=True)
    files = os.listdir(tmp_path)
    assert len(files) == 1
    with open(tmp_path / files[0], 'rb') as f:
        assert f.read() == events.seen[0]['file']


def test_two_messages_one_connection_get_fresh_files(tmp_path):
    events = Capture()

    async def go():
        srv = SmtpdServer(events, settings={'spool': str(tmp_path) + '/'}, debug=True)
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
        loop = asyncio.get_event_loop()

        def client():
            import smtplib
            c = smtplib.SMTP('127.0.0.1', port, timeout=10)
            c.ehlo('twice')
            for body in ('first', 'second'):
                c.mail('a@x.org')
                c.rcpt('b@y.org')
                c.data(f'Subject: {body}\r\n\r\n{body}')
            c.quit()

        try:
            await loop.run_in_executor(None, client)
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(go())
    assert len(events.seen) == 2
    assert events.seen[0]['path'] != events.seen[1]['path']
    assert events.seen[0]['file'].endswith(b'first')
    assert events.seen[1]['file'].endswith(b'second')


# --- takeover via keep() -------------------------------------------------------


def test_keep_returns_path_and_file_survives(tmp_path):
    events = KeepCapture()
    _run(events, {'spool': str(tmp_path) + '/'})
    kept = events.seen[0]['kept']
    assert kept is not None and os.path.exists(kept)
    with open(kept, 'rb') as f:
        assert f.read() == events.seen[0]['file']


def test_keep_moves_to_target(tmp_path):
    events = MoveCapture()
    _run(events, {'spool': str(tmp_path) + '/'})
    target = events.seen[0]['target']
    assert os.path.exists(target)
    with open(target, 'rb') as f:
        assert f.read() == events.seen[0]['file']
    assert os.listdir(tmp_path) == [os.path.basename(target)]


def test_early_keep_mid_stream_completes_at_target(tmp_path):
    # move the growing file on the first body line; the body (larger than the
    # write buffer) keeps flowing in and the regular finalize chomps + closes
    events = EarlyMoveCapture()
    body = 'X' * 20000
    _run(events, {'spool': str(tmp_path) + '/'}, messages=(f'Subject: t\r\n\r\n{body}\r\nlast',))
    size = os.path.getsize(events.target)
    with open(events.target, 'rb') as f:
        content = f.read()
    assert size == events.seen[0]['bytesize']
    assert content.startswith(b'Subject: t\r\n\r\nXXX') and content.endswith(b'\r\nlast')


def test_manual_rename_takeover_still_safe(tmp_path):
    events = RenameCapture()
    _run(events, {'spool': str(tmp_path) + '/'})   # unlink must not raise
    target = events.seen[0]['target']
    assert os.path.exists(target)
    with open(target, 'rb') as f:
        assert f.read() == events.seen[0]['file']


# --- aborts and caps -----------------------------------------------------------


def _abort_mid_data(tmp_path, debug):
    async def go():
        srv = SmtpdServer(Capture(), settings={'spool': str(tmp_path) + '/'}, debug=debug)
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
        loop = asyncio.get_event_loop()

        def client():
            import smtplib
            c = smtplib.SMTP('127.0.0.1', port, timeout=10)
            c.ehlo('abort')
            c.mail('a@x.org')
            c.rcpt('b@y.org')
            code, _ = c.docmd('DATA')
            assert code == 354
            c.send(b'Subject: gets aborted\r\n\r\nhalf a body\r\n')
            c.close()  # lost connection mid-DATA

        try:
            await loop.run_in_executor(None, client)
            await asyncio.sleep(0.4)
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(go())


def test_abort_mid_data_cleans_up(tmp_path):
    _abort_mid_data(tmp_path, debug=False)
    assert os.listdir(tmp_path) == []


def test_abort_mid_data_debug_keeps_the_file(tmp_path):
    _abort_mid_data(tmp_path, debug=True)
    assert len(os.listdir(tmp_path)) == 1


def test_data_size_cap_works_in_spool_mode(tmp_path):
    import smtplib

    async def go():
        srv = SmtpdServer(Capture(), settings={'spool': str(tmp_path) + '/', 'data_size': 64})
        await srv.start([{'host': '127.0.0.1', 'port': 0}])
        port = srv._servers[0].sockets[0].getsockname()[1]
        loop = asyncio.get_event_loop()

        def client():
            c = smtplib.SMTP('127.0.0.1', port, timeout=10)
            c.ehlo('cap')
            c.mail('a@x.org')
            c.rcpt('b@y.org')
            # smtplib returns the final reply after the dot as a tuple
            code, _ = c.data('Subject: big\r\n\r\n' + 'X' * 200)
            assert code == 552
            c.close()

        try:
            await loop.run_in_executor(None, client)
            await asyncio.sleep(0.2)
        finally:
            await srv.stop(wait_seconds_before_close=0.3)

    asyncio.run(go())
    assert os.listdir(tmp_path) == []   # aborted file cleaned up


def test_missing_spool_directory_fails_at_start(tmp_path):
    with pytest.raises(ValueError):
        SmtpdServer(Capture(), settings={'spool': str(tmp_path / 'nope' / 'mx1-')})
