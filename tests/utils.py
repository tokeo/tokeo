"""
The helpers for all tests of the app.

General tools every test may import: the root of the project, the marker of
the stress tests, waiting for a condition and running a program in a pseudo
terminal. They hold no asserts, the test asserts on what they return.

### Example

```python
from tests.utils import ROOT, stresstest, wait_until, wait_until_async
from tests.utils import wait_process_group_ended, Terminal
```

The stress tests run only with ```make test STRESSTESTS=1```, marked by
```stresstest```:

```python
pytestmark = stresstest          # all tests of the file


@stresstest                      # one test
def test_worker_stress_hands_every_message_on_once():
    ...
```

### Notes

- Only code that is truly general and needed again and again goes here,
    code of one subject stays with that subject

"""

import os
import time
import asyncio
import select
import subprocess
import pytest

#: The root of the project, the working directory of every program a test
#: starts, e.g. ```subprocess.Popen([...], cwd=ROOT)```.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The marker of the stress tests, they run only with
#: ```make test STRESSTESTS=1```. Set it on all tests of a file by
#: ```pytestmark = stresstest``` or on one test by ```@stresstest```.
stresstest = pytest.mark.skipif(os.environ.get('STRESSTESTS', '0') != '1', reason='set STRESSTESTS=1 to run the stress tests')


def wait_until(check, timeout=5.0, interval=0.02):
    """
    Call check until it holds or the timeout ends, return its last value.

    ### Args

    - **check** (callable): Called without arguments, holds when its value
        is true
    - **timeout** (float): The seconds to wait at most
    - **interval** (float): The seconds between two calls

    ### Returns

    - **Any**: The last value of check, true when it held in time

    ### Example

    ```python
    # the assert fails with the last value of the check
    assert wait_until(lambda: len(list(tmp_path.glob('pid_*'))) == 3, 3.0)

    # the value itself, e.g. the first line that arrived
    line = wait_until(lambda: next(iter(received), None))
    ```

    """
    deadline = time.monotonic() + timeout
    while not (result := check()) and time.monotonic() < deadline:
        time.sleep(interval)
    return result


def wait_process_group_ended(pgid, timeout=5.0):
    """
    Wait until every process of the group ended, return whether they did.

    A program started with ```start_new_session=True``` leads its own group,
    its pid is the id of the group, its forked workers belong to it.

    ### Args

    - **pgid** (int): The id of the group, the pid of the started program
    - **timeout** (float): The seconds to wait at most

    ### Returns

    - **bool**: True when no process of the group is left

    ### Example

    ```python
    def test_program_ends_with_its_group():
        args = [sys.executable, '-c', 'import time; time.sleep(30)']
        proc = subprocess.Popen(args, start_new_session=True, cwd=ROOT)
        try:
            proc.terminate()
            assert proc.wait(timeout=8) != 0
            # the program ended, and so did every process it forked
            assert wait_process_group_ended(proc.pid)
        finally:
            proc.kill()
    ```

    """

    def ended():
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        return False

    return wait_until(ended, timeout)


async def wait_until_async(check, timeout=5.0, interval=0.02):
    """
    Like wait_until inside a coroutine, the event loop goes on meanwhile.

    ### Args

    - **check** (callable): Called without arguments, holds when its value
        is true
    - **timeout** (float): The seconds to wait at most
    - **interval** (float): The seconds between two calls

    ### Returns

    - **Any**: The last value of check, true when it held in time

    ### Example

    ```python
    def test_server_counts_its_connections():
        async def go():
            srv, port = await start_server()
            ...
            assert await wait_until_async(lambda: srv.connections == 0)

        asyncio.run(go())
    ```

    """
    deadline = time.monotonic() + timeout
    while not (result := check()) and time.monotonic() < deadline:
        await asyncio.sleep(interval)
    return result


class Terminal:
    """
    Run a program in a pseudo terminal and read its screen by pyte.

    The terminal answers the cursor position requests like a real one;
    with ```ctty``` it is the controlling terminal, so Ctrl-C sends SIGINT.
    Without the package pyte the test is skipped, without a pseudo terminal,
    as on Windows, it fails; mark such a test with a skip for that platform.

    ### Args

    - **args** (list): The program and its arguments, started in ```ROOT```
    - **rows** / **columns** (int): The size of the screen
    - **ctty** (bool): Make the terminal the controlling one of the program
    - **timeout** (float): The seconds ```wait_for``` waits at most in all

    ### Example

    ```python
    def test_prompt_greets():
        args = [sys.executable, '-c', 'print("hello", input("name> "))']
        with Terminal(args, rows=5, columns=40) as terminal:
            assert terminal.wait_for(lambda lines: lines[-1] == 'name>')
            terminal.send(b'anna\\r')
            code = terminal.wait()
        # lines are the screen rows with text, raw is every byte read
        assert terminal.lines[-1] == 'hello anna' and code == 0
    ```

    An app of the tests runs as a probe of its subject, started as module:

    ```python
    args = [sys.executable, '-m', 'tests.<folder>.<subject>_tests_<part>_probe']
    ```

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
