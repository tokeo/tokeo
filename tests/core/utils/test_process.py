"""
Tests for tokeo.core.utils.process: the name of a process, the crash file of a
hard crash and the dump of the stacks of all threads on a signal.

Every test runs its probe in a process of its own, the helpers change the
process they run in.
"""

import sys
import signal
import subprocess

import pytest

from tests.utils import ROOT_DIR


def _run_probe(code, *args):
    # run the code in a fresh python with the args, return the finished process
    return subprocess.run([sys.executable, '-c', code, *args], cwd=ROOT_DIR, capture_output=True, text=True, timeout=30)


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='the kernel name of a process is read from /proc')
def test_set_process_name_sets_the_kernel_name():
    """The kernel keeps 15 bytes of the name, set_process_name returns True."""
    code = '\n'.join(
        [
            'from tokeo.core.utils.process import set_process_name',
            "print(set_process_name('tokeo-probe-name-long'))",
            "print(open('/proc/self/comm').read().strip())",
        ]
    )
    done_process = _run_probe(code)
    assert done_process.stdout.splitlines() == ['True', 'tokeo-probe-nam']


def test_enable_crash_file_writes_the_stacks_of_a_hard_crash(tmp_path):
    """An abort writes the fatal error and the stack of the thread into the file."""
    crash_path = tmp_path / 'probe.crash'
    code = '\n'.join(
        [
            'import os, sys',
            'from tokeo.core.utils.process import enable_crash_file',
            'enable_crash_file(sys.argv[1])',
            'os.abort()',
        ]
    )
    done_process = _run_probe(code, str(crash_path))
    assert done_process.returncode == -signal.SIGABRT
    crash_text = crash_path.read_text()
    assert 'Fatal Python error: Aborted' in crash_text
    assert 'File "<string>", line 4 in <module>' in crash_text


def test_register_thread_dump_writes_the_stacks_and_runs_on(tmp_path):
    """The signal writes the stacks of all threads, the process goes on."""
    dump_path = tmp_path / 'probe.dump'
    code = '\n'.join(
        [
            'import os, sys, signal',
            'from tokeo.core.utils.process import register_thread_dump',
            "dump_file = open(sys.argv[1], 'a')",
            'register_thread_dump(signal.SIGUSR1, dump_file)',
            'os.kill(os.getpid(), signal.SIGUSR1)',
            "print('alive')",
        ]
    )
    done_process = _run_probe(code, str(dump_path))
    assert (done_process.returncode, done_process.stdout) == (0, 'alive\n')
    assert 'File "<string>", line 5 in <module>' in dump_path.read_text()
