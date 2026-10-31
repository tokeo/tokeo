"""
Tests for tokeo.ext.argparse: the Controller of tokeo formats its help by
TokeoHelpFormatter, which lists the commands sorted by name in one aligned
column with their expanded help, and keeps descriptions and epilogs as they
are written, followed by a spacer line.
"""

import argparse

import pytest
from cement import ex
from tokeo.main import TokeoTest
from tokeo.ext.argparse import Controller, TokeoHelpFormatter

DESCRIPTION = 'First line\n  second line indented\n\nafter a blank line'


class ArgparseBaseController(Controller):
    """The base controller, its commands defined out of alphabetical order."""

    class Meta:
        label = 'base'
        description = DESCRIPTION
        epilog = 'the epilog'

    @ex(help='zeta help')
    def zeta(self):
        pass

    @ex(help='alpha help of %(prog)s at 100%%')
    def alpha(self):
        pass


class ArgparseNestedController(Controller):
    """A controller nested below the base controller."""

    class Meta:
        label = 'nested'
        stacked_on = 'base'
        stacked_type = 'nested'
        help = 'nested help'

    @ex(help='inner help')
    def inner(self):
        pass


class ArgparseTest(TokeoTest):
    """A test app with a base and a nested controller of tokeo."""

    class Meta:
        handlers = [
            ArgparseBaseController,
            ArgparseNestedController,
        ]


@pytest.fixture(autouse=True)
def no_colors(monkeypatch):
    """No colors in the help, as python 3.14 adds them when forced by the shell."""
    monkeypatch.setenv('PYTHON_COLORS', '0')


def _help(argv, capsys):
    # the help the test app prints for argv, a --help ends in SystemExit
    with ArgparseTest(argv=argv) as app:
        try:
            app.run()
        except SystemExit:
            pass
    return capsys.readouterr().out


def _commands(text):
    # the lines of the command list below "sub-commands:" up to the blank line
    lines = text.split('sub-commands:\n', 1)[1].split('\n')
    end = lines.index('')
    return lines[1:end]


def _parser(*commands):
    # a parser of TokeoHelpFormatter with the given (name, help) commands
    parser = argparse.ArgumentParser(prog='prog', formatter_class=TokeoHelpFormatter)
    subparsers = parser.add_subparsers(metavar='')
    for name, text in commands:
        subparsers.add_parser(name, help=text)
    return parser


# Controller


def test_controller_parsers_use_the_tokeo_help_formatter():
    """The base and the nested controller both format by TokeoHelpFormatter."""
    # the controllers build their parsers when the app runs
    with ArgparseTest(argv=['nested', 'inner']) as app:
        app.run()
        controllers = {c._meta.label: c for c in app.controller._controllers}
        assert app.args.formatter_class is TokeoHelpFormatter
        assert controllers['nested']._parser.formatter_class is TokeoHelpFormatter


def test_controller_without_a_command_prints_its_help(capsys):
    """Without a command the base controller prints its help and returns."""
    text = _help([], capsys)
    assert text.startswith('usage: tokeo_test')
    assert 'sub-commands:' in text


def test_controller_nested_without_a_command_prints_its_help(capsys):
    """'nested' without a command prints the help of the nested controller."""
    text = _help(['nested'], capsys)
    assert text.startswith('usage: tokeo_test nested')
    assert _commands(text) == ['  inner    inner help']


def test_controller_help_exits_with_0(capsys):
    """--help prints the help and exits with 0."""
    with ArgparseTest(argv=['--help']) as app:
        with pytest.raises(SystemExit) as raised:
            app.run()
    assert raised.value.code == 0
    assert 'sub-commands:' in capsys.readouterr().out


# TokeoHelpFormatter._iter_indented_subactions


def test_help_lists_the_commands_sorted_by_name(capsys):
    """zeta, alpha and the nested controller are listed as alpha, nested, zeta."""
    names = [line.split()[0] for line in _commands(_help(['--help'], capsys))]
    assert names == ['alpha', 'nested', 'zeta']


def test_iter_indented_subactions_keeps_the_order_of_other_actions():
    """Subactions of an action other than subparsers keep their order."""

    class Grouped(argparse.Action):
        # an action that groups other actions, like a custom one may do
        def _get_subactions(self):
            return ['b', 'a']

    formatter = TokeoHelpFormatter('prog')
    assert list(formatter._iter_indented_subactions(Grouped(['-g'], 'g'))) == ['b', 'a']


def test_iter_indented_subactions_of_an_object_without_subactions_is_empty():
    """An object without _get_subactions yields nothing."""
    formatter = TokeoHelpFormatter('prog')
    assert list(formatter._iter_indented_subactions(object())) == []


# TokeoHelpFormatter._format_action


def test_help_aligns_the_help_of_the_commands(capsys):
    """Each help starts 4 spaces after the longest command name, 'nested'."""
    gap, column = len('  nested'), len('  nested    ')
    lines = _commands(_help(['--help'], capsys))
    assert [line[gap:column] for line in lines] == ['    '] * 3
    assert [line[column] for line in lines] == ['a', 'n', 'z']


def test_help_of_a_command_is_expanded(capsys):
    """%(prog)s and %% in the help of a command are expanded."""
    assert '  alpha     alpha help of tokeo_test at 100%' in _commands(_help(['--help'], capsys))


def test_format_action_of_a_command_without_help_shows_its_name():
    """A command added with help None is listed by its name alone."""
    text = _parser(('first', 'first help'), ('second', None)).format_help()
    assert '  second    \n' in text
    assert '  first     first help\n' in text


def test_format_action_of_subparsers_without_commands():
    """Subparsers without a command format their help without an error."""
    parser = argparse.ArgumentParser(prog='prog', formatter_class=TokeoHelpFormatter)
    parser.add_subparsers(title='commands')
    assert 'commands:' in parser.format_help()


# TokeoHelpFormatter._fill_text


def test_fill_text_indents_each_line_and_appends_a_spacer():
    """Each line keeps its breaks, gets the indent and ' \\n ' follows."""
    formatter = TokeoHelpFormatter('prog')
    assert formatter._fill_text('one\n  two\n\nthree', 10, '> ') == '> one\n>   two\n> \n> three \n '


def test_help_keeps_the_description_and_epilog_as_written(capsys):
    """The description is not rewrapped and both texts end with the spacer."""
    text = _help(['--help'], capsys)
    assert f'{DESCRIPTION} \n \n' in text
    assert text.endswith('the epilog \n \n')
