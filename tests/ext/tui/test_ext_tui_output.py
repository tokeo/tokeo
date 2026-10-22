"""
Tests for tokeo.ext.tui.output and the shared parts of tokeo.ext.tui.
"""

import io
import signal

import pytest
from rich.console import Console
from cement.core.exc import CaughtSignal
from tokeo.main import TokeoTest
from tokeo.ext.tui import TokeoTuiError, require
from tokeo.ext.tui.output import TokeoTuiOutput


class OutputTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.output',
        ]


@pytest.fixture
def output():
    # writes to a buffer of a fixed width instead of the terminal
    with OutputTest() as app:
        buffer = io.StringIO()
        yield TokeoTuiOutput(app, file=buffer, width=60), buffer


def test_extension_registers_output_only():
    with OutputTest() as app:
        assert isinstance(app.tui.output, TokeoTuiOutput)
        with pytest.raises(TokeoTuiError, match='Enable the extension tokeo.ext.tui.input to use app.tui.input!'):
            app.tui.input


def test_require_names_the_missing_package():
    with pytest.raises(TokeoTuiError, match='The extension tokeo.ext.tui.output needs the package nope, add it'):
        require('tokeo_no_such_module', 'nope', 'tokeo.ext.tui.output')


def test_table(output):
    out, _ = output
    table = out.table(['queue', ('ready', 'right')], [['mail', 3], ['orders_unparalleled', None]], title='queues')
    lines = [line for line in out.render(table).splitlines() if line.strip()]
    assert lines[0].rstrip() == 'queues'
    assert lines[1].split() == ['queue', 'ready']
    # right aligned numbers end in the same column, None shows empty
    mail = next(line for line in lines if line.strip().startswith('mail'))
    orders = next(line for line in lines if 'orders_unparalleled' in line)
    assert mail.rstrip().endswith('3') and orders.split() == ['orders_unparalleled']
    assert len(mail.rstrip()) == len(lines[1].rstrip())


def test_data_is_never_read_as_markup(output):
    out, buffer = output
    out.print('[bold]args[/bold]', out.table(['args'], [["(('[red]x', 1), {})"]]))
    assert '[bold]args[/bold]' in buffer.getvalue()
    assert "(('[red]x', 1), {})" in buffer.getvalue()


def test_print_to_a_pipe_has_no_control_characters(output):
    out, buffer = output
    assert not out.is_terminal
    out.print(out.table(['a'], [[1]]))
    assert '\x1b' not in buffer.getvalue()


def test_follow_appends_blocks_without_terminal(output):
    out, buffer = output
    ticks = iter(range(1, 10))
    out.follow(lambda: [f'tick {next(ticks)}', 'done'], interval=0, count=2)
    assert buffer.getvalue() == 'tick 1\ndone\n\ntick 2\ndone\n'


def test_follow_repaints_in_a_terminal(output):
    out, _ = output
    buffer = io.StringIO()
    out._console = Console(file=buffer, width=60, force_terminal=True)
    ticks = iter(range(1, 10))
    out.follow(lambda: f'tick {next(ticks)}', interval=0, count=2)
    text = buffer.getvalue()
    # the alternate screen shows the former content again at the end
    assert text.startswith('\x1b[?1049h') and '\x1b[?1049l' in text
    assert 'tick 1' in text and 'tick 2' in text


def test_follow_appends_in_a_terminal_on_request(output):
    out, _ = output
    buffer = io.StringIO()
    out._console = Console(file=buffer, width=60, force_terminal=True)
    out.follow(lambda: '{"a": 1}', interval=0, repaint=False, count=2)
    assert buffer.getvalue().count('{"a": 1}') == 2 and '\x1b[?1049h' not in buffer.getvalue()


@pytest.mark.parametrize('stop', [KeyboardInterrupt(), CaughtSignal(signal.SIGINT, None), CaughtSignal(signal.SIGTERM, None)])
def test_follow_ends_on_ctrl_c_and_shutdown_signals(output, stop):
    out, buffer = output

    def render():
        raise stop

    out.follow(render, interval=0)
    assert buffer.getvalue() == ''


def test_follow_passes_other_signals(output):
    out, _ = output

    def render():
        raise CaughtSignal(signal.SIGUSR1, None)

    with pytest.raises(CaughtSignal):
        out.follow(render, interval=0)
