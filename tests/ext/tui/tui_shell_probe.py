"""
A tokeo app with a tui shell, run by test_ext_tui_input in a pseudo terminal.

Half a second after the start another thread prints, logs and calls
app.print, the shell ends on the line exit.
"""

import time
import threading
from tokeo.main import TokeoTest


class ProbeApp(TokeoTest):

    class Meta:
        label = 'probe'
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
        ]


def command(line):
    if line == 'exit':
        raise EOFError()
    return True


def other_thread(app):
    time.sleep(0.5)
    print('MARK-PRINT')
    app.log.warning('MARK-LOG')
    app.print('MARK-APP-PRINT')


if __name__ == '__main__':
    with ProbeApp() as app:
        threading.Thread(target=other_thread, args=(app,), daemon=True).start()
        app.tui.input.shell(command, prompt='Probe> ')
