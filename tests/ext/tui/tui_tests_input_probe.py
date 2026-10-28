"""
A tokeo app with a tui shell, run by test_input in a pseudo terminal.

Half a second after the start another thread prints, logs and calls
app.print, the line slow runs a command of 3 seconds, closestdin closes stdin
and devnull moves it to /dev/null, the shell ends on the line exit. With a
file name as argument the probe logs into that file as well and writes
SHELL-ENDED to it once the shell ended.
"""

import os
import sys
import time
import logging
import threading
from tests.ext.tui.tui_tests_helper import TuiInputTest


class TuiInputProbeTest(TuiInputTest):

    class Meta:
        label = 'probe'


def command(line):
    if line == 'exit':
        raise EOFError()
    if line == 'slow':
        print('MARK-SLOW-START', flush=True)
        time.sleep(3)
        print('MARK-SLOW-END', flush=True)
    if line == 'closestdin':
        sys.stdin.close()
    if line == 'devnull':
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    return True


def other_thread(app):
    time.sleep(0.5)
    print('MARK-PRINT')
    app.log.warning('MARK-LOG')
    app.print('MARK-APP-PRINT')


if __name__ == '__main__':
    with TuiInputProbeTest() as app:
        log_file = sys.argv[1] if len(sys.argv) > 1 else None
        if log_file:
            app.log.backend.addHandler(logging.FileHandler(log_file))
        threading.Thread(target=other_thread, args=(app,), daemon=True).start()
        app.tui.input.shell(command, prompt='Probe> ')
        if log_file:
            with open(log_file, 'a') as f:
                f.write('SHELL-ENDED\n')
