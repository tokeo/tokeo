"""
The helpers for all tests of the app.
"""

import os
import time
import asyncio
import select
import subprocess
import pytest

# the root of the project, working directory of the programs the tests start
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# the stress tests run only with STRESSTESTS=1, e.g. make test STRESSTESTS=1
stresstest = pytest.mark.skipif(os.environ.get('STRESSTESTS', '0') != '1', reason='set STRESSTESTS=1 to run the stress tests')


def wait_until(check, timeout=5.0, interval=0.02):
    """Call check until it holds or the timeout ends, return its last value."""
    deadline = time.monotonic() + timeout
    while not (result := check()) and time.monotonic() < deadline:
        time.sleep(interval)
    return result


def wait_process_group_ended(pgid, timeout=5.0):
    """
    Wait until every process of the group ended, return whether they did.

    A program started with ```start_new_session=True``` leads its own group,
    its pid is the id of the group, its forked workers belong to it.
    """

    def ended():
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        return False

    return wait_until(ended, timeout)


async def wait_until_async(check, timeout=5.0, interval=0.02):
    """Like wait_until inside a coroutine, the event loop goes on meanwhile."""
    deadline = time.monotonic() + timeout
    while not (result := check()) and time.monotonic() < deadline:
        await asyncio.sleep(interval)
    return result


class Terminal:
    """
    Run a program in a pseudo terminal and read its screen by pyte.

    The terminal answers the cursor position requests like a real one;
    with ```ctty``` it is the controlling terminal, so Ctrl-C sends SIGINT.
    """

    def __init__(self, args, rows=24, columns=80, ctty=False, timeout=30):
        self.args, self.rows, self.columns, self.ctty, self.timeout = args, rows, columns, ctty, timeout

    def __enter__(self):
        pyte = pytest.importorskip('pyte')
        import pty
        import fcntl
        import struct
        import termios

        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', self.rows, self.columns, 0, 0))
        env = dict(os.environ, TERM='xterm-256color')
        session = dict(start_new_session=True, preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0)) if self.ctty else {}
        self.process = subprocess.Popen(self.args, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=ROOT, **session)
        os.close(slave)
        self.screen = pyte.Screen(self.columns, self.rows)
        self.stream = pyte.ByteStream(self.screen)
        self.raw = b''
        self.deadline = time.monotonic() + self.timeout
        return self

    def __exit__(self, *exc):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(10)
        os.close(self.master)

    @property
    def lines(self):
        """The lines of the screen with text."""
        return [line.rstrip() for line in self.screen.display if line.strip()]

    def _read(self, wait):
        # True on output, None without, False once the program closed the terminal
        if not select.select([self.master], [], [], wait)[0]:
            return None
        try:
            data = os.read(self.master, 65536)
        except OSError:
            return False
        self.raw += data
        self.stream.feed(data)
        for _ in range(data.count(b'\x1b[6n')):
            os.write(self.master, f'\x1b[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R'.encode())
        return True

    def wait_for(self, check):
        """Read until ```check(lines)``` holds, return the lines or None."""
        while time.monotonic() < self.deadline and self.process.poll() is None:
            lines = self.lines
            if lines and check(lines):
                return lines
            if self._read(0.05) is False:
                break
        return None

    def send(self, keys):
        """Type the keys, e.g. ```b'exit\\r'```."""
        os.write(self.master, keys)

    def wait(self, timeout=10):
        """Wait for the end of the program and its output, return its exit code."""
        code = self.process.wait(timeout)
        while self._read(0.2):
            pass
        return code
