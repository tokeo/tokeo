"""
A tokeo app with a tui shell, run by test_input in a pseudo terminal.

Half a second after the start another thread prints, logs and calls
app.print, the line slow runs a command of 3 seconds, the shell ends on the
line exit.
"""

import time
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
    return True


def other_thread(app):
    time.sleep(0.5)
    print('MARK-PRINT')
    app.log.warning('MARK-LOG')
    app.print('MARK-APP-PRINT')


if __name__ == '__main__':
    with TuiInputProbeTest() as app:
        threading.Thread(target=other_thread, args=(app,), daemon=True).start()
        app.tui.input.shell(command, prompt='Probe> ')
