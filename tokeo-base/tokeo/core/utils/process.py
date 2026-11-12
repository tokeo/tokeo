"""
Helpers for the process of a service: its name, a crash file and a dump of the
stacks of all threads, with the standard library only.

"""

import sys
import ctypes
import faulthandler

# the option of prctl that names the calling thread (Linux)
_PR_SET_NAME = 15


def set_process_name(name):
    """
    Set the name of the process that ps, top and the kernel log show.

    ### Args

    - **name** (str): The name of the process

    ### Returns

    - **bool**: True when the name is set, False off Linux without setproctitle

    ### Notes

    : On Linux prctl sets the name the kernel keeps, at most 15 bytes, so the
        kernel log of an OOM kill names the process; call it from the main
        thread.

    : With setproctitle installed the whole command line shows the name, also
        off Linux.

    """
    named = False
    try:
        import setproctitle
    except ImportError:
        pass
    else:
        setproctitle.setproctitle(name)
        named = True
    if sys.platform.startswith('linux'):
        libc = ctypes.CDLL(None, use_errno=True)
        named = libc.prctl(_PR_SET_NAME, name.encode()[:15], 0, 0, 0) == 0 or named
    return named


def enable_crash_file(path):
    """
    Write the stacks of all threads into a file on a hard crash of the process.

    ### Args

    - **path** (str): The file, opened to append

    ### Returns

    - **file**: The open file, it stays open for the life of the process

    ### Notes

    : A hard crash is a segfault, an abort or another fatal signal, where
        Python writes no traceback of its own.

    """
    crash_file = open(path, 'a')
    faulthandler.enable(file=crash_file, all_threads=True)
    return crash_file


def register_thread_dump(signum, file):
    """
    Write the stacks of all threads into a file on a signal, the process runs on.

    ### Args

    - **signum** (int): The signal, e.g. ```signal.SIGUSR1```
    - **file** (file): The open file the stacks are written to

    """
    faulthandler.register(signum, file=file, all_threads=True)
