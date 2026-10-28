"""
Tokeo TUI Input Extension Module.

An interactive shell for the terminal, available as ```app.tui.input``` once
```tokeo.ext.tui.input``` is enabled. Built on prompt_toolkit, which is loaded
when the extension is enabled.

### Features

- Command history, suggestions from the history and completion while typing
- Output of other threads, prints and log lines alike, appears above the
    prompt, which is drawn again below it
- Ctrl-C clears the line or aborts the running command, Ctrl-D and the
    shutdown signals SIGTERM and SIGHUP end the shell, also while the prompt
    waits

### Example

```python
def command(line):
    if line in ('exit', 'quit'):
        raise EOFError()
    app.print(f'you entered {line}')
    # True clears the line, False keeps it for a correction
    return True

completer = {'exit': None, 'quit': None}
app.tui.input.shell(command, prompt='MyApp> ', completer=completer)
```

"""

import sys
import signal
import logging
import threading
from contextlib import contextmanager
from cement.core.exc import CaughtSignal
from cement.core.foundation import SIGNALS
from tokeo.ext.tui import register, require


class TokeoTuiInput:
    """
    Interactive shell for the terminal.

    ### Args

    - **app** (Application): The Cement application instance

    """

    def __init__(self, app):
        self.app = app

    def shell(self, command, prompt='> ', completer=None, history=None, **session):
        """
        Run a shell until Ctrl-D, a shutdown signal or an EOFError of a command.

        ### Args

        - **command** (callable): Called with each entered line; returns True
            to clear the line, or False to keep it for a correction
        - **prompt** (str|callable): The prompt, a callable is asked on each
            redraw, e.g. to show a state
        - **completer** (dict|Completer, optional): A nested dict of words
            like ```{'tasks': {'pause': None}}```, or a prompt_toolkit completer
        - **history** (list|History, optional): The history, a list of lines to
            start with, or a prompt_toolkit history; a new one without
        - **session**: More options for the prompt_toolkit ```PromptSession```

        ### Notes

        : A failing command is logged as error and its line is kept, the shell
            goes on

        : Ctrl-C, also a SIGINT while a command runs, aborts only the line or
            the command; SIGTERM and SIGHUP end the shell

        """
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import NestedCompleter
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.patch_stdout import patch_stdout

        if isinstance(completer, dict):
            completer = NestedCompleter.from_nested_dict(completer)
        if history is None or isinstance(history, (list, tuple)):
            history = InMemoryHistory(history or [])
        # complete while typing excludes history search, so it is not set
        prompt_session = PromptSession(
            history=history,
            completer=completer,
            auto_suggest=AutoSuggestFromHistory(),
            complete_while_typing=True,
            **session,
        )
        consoles = (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__)
        line = ''
        # raw keeps the colors; sys.stdout is the proxy of patch_stdout for the logs
        with patch_stdout(raw=True), _logs_to(sys.stdout, consoles), _ends_by_signals(prompt_session):
            while True:
                try:
                    line = prompt_session.prompt(prompt, default=line)
                    if command(line):
                        line = ''
                except KeyboardInterrupt:
                    line = ''
                except EOFError:
                    break
                except CaughtSignal as e:
                    # a SIGINT while a command runs aborts only the command
                    if e.signum == signal.SIGINT:
                        line = ''
                        continue
                    if e.signum in SIGNALS:
                        break
                    raise
                except Exception as e:
                    self.app.log.error(f'Shell command failed: {e}')


@contextmanager
def _logs_to(stream, consoles):
    """Write the log handlers of the consoles to the stream meanwhile."""
    loggers = [logging.getLogger(), *(item for item in logging.Logger.manager.loggerDict.values() if isinstance(item, logging.Logger))]
    # log handlers keep the stream they were set up with, patch_stdout misses them
    moved = [
        (handler, handler.setStream(stream))
        for logger in loggers
        for handler in logger.handlers
        if isinstance(handler, logging.StreamHandler) and handler.stream in consoles
    ]
    try:
        yield
    finally:
        for handler, original in moved:
            handler.setStream(original)


@contextmanager
def _ends_by_signals(prompt_session):
    """End the shell by SIGTERM and SIGHUP, also while the prompt waits."""
    # signal handlers are set by the main thread only
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def end(signum, frame):
        try:
            # the handler of the app, e.g. cement runs its signal hooks and raises
            previous[signum](signum, frame)
        except CaughtSignal as caught:
            # raised in the event loop of the prompt, prompt_toolkit would catch
            # it and ask for ENTER, so the prompt ends with it instead
            if not prompt_session.app.is_running:
                raise
            prompt_session.app.exit(exception=caught)

    # only signals the app handles, a default or ignored signal stays as it is
    previous = {signum: signal.getsignal(signum) for signum in SIGNALS if signum != signal.SIGINT}
    previous = {signum: handler for signum, handler in previous.items() if callable(handler)}
    for signum in previous:
        signal.signal(signum, end)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def load(app):
    """
    Load the tui input extension, it registers ```app.tui.input```.

    ### Args

    - **app** (Application): The Cement application instance

    ### Raises

    - **TokeoTuiError**: If the package prompt_toolkit is not installed

    """
    require('prompt_toolkit', 'prompt_toolkit', 'tokeo.ext.tui.input')
    register(app, 'input', TokeoTuiInput(app))
