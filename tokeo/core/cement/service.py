"""
The start of the process of a service: its name, a crash file, a dump of the
stacks of all threads and a line at its start and at its end.
"""

import os
import sys
import time
import atexit
import signal
from tokeo.core.exc import TokeoError
from tokeo.core.utils.process import set_process_name, enable_crash_file, register_thread_dump


class TokeoServiceError(TokeoError, ValueError):
    """
    Exception raised for a service setting of the app config that cannot be used.

    ### Notes

    : Inherits from TokeoError, so main reports it as a clean message, and from
        ValueError for a wrong value

    """

    pass


def _get_setting(app, key):
    # a setting of the config section of the app, None when it is not set
    section = app._meta.label
    if app.config.has_section(section) and key in app.config.keys(section):
        return app.config.get(section, key)
    return None


def _log_end(app, name, started_at, caught_signums):
    # the end line, at the exit of the process when the exit code is final
    runtime = time.monotonic() - started_at
    if caught_signums:
        reason = f'by {signal.Signals(caught_signums[-1]).name}'
    else:
        reason = 'normally' if app.exit_code == 0 else 'with an error'
    app.log.debug(f'service {name} ended {reason} after {runtime:.1f} s (exit code {app.exit_code})')


def start_service(app, service, number=None):
    """
    Prepare the process of a service: name, crash file, thread dump, start line.

    ### Args

    - **app** (Application): The Cement application instance
    - **service** (str): The name of the service, e.g. ```scheduler```
    - **number** (int|str, optional): The number of a worker process

    ### Raises

    - **TokeoServiceError**: If ```crash_dir``` is no text or
        ```thread_dump_signal``` names no signal

    ### Notes

    : The process is named ```<app label>-<service>[-<number>]```; the kernel
        keeps 15 bytes of it. Off Linux without setproctitle the name is not
        set and a warning tells so.

    : ```crash_dir``` and ```thread_dump_signal``` of the config section of the
        app are null by default: no crash file, no thread dump. The crash file
        is ```<crash_dir>/<name>.crash```; the thread dump goes there, else to
        stderr.

    : The end line follows at the exit of the process with the signal that
        ended it, the runtime and the exit code; a forked worker that ends by
        ```os._exit``` writes none.

    """
    # check both settings before the process is changed
    crash_dir = _get_setting(app, 'crash_dir')
    if crash_dir is not None and (not isinstance(crash_dir, str) or not crash_dir):
        raise TokeoServiceError(f'{app._meta.label}.crash_dir must be a directory, not {crash_dir!r}')
    dump_signal = _get_setting(app, 'thread_dump_signal')
    dump_signum = getattr(signal, dump_signal, None) if isinstance(dump_signal, str) else None
    if dump_signal is not None and not isinstance(dump_signum, signal.Signals):
        raise TokeoServiceError(f'{app._meta.label}.thread_dump_signal must name a signal like SIGUSR1, not {dump_signal!r}')
    name = f'{app._meta.label}-{service}' + ('' if number is None else f'-{number}')
    if not set_process_name(name):
        app.log.warning(f'service {name}: the process name is not set on this platform')
    crash_file = None
    if crash_dir is not None:
        os.makedirs(crash_dir, exist_ok=True)
        crash_file = enable_crash_file(os.path.join(crash_dir, f'{name}.crash'))
    if dump_signal is not None:
        register_thread_dump(dump_signum, crash_file or sys.stderr)
    # cement runs the hook signal before it raises CaughtSignal
    caught_signums = []
    app.hook.register('signal', lambda app, signum, frame: caught_signums.append(signum))
    atexit.register(_log_end, app, name, time.monotonic(), caught_signums)
    app.log.debug(f'service {name} started (pid {os.getpid()})')
