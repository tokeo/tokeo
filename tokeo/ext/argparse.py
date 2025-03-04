"""
Custom command-line argument handling for Tokeo applications.

This module extends Cement's ArgparseController with an improved help formatter
that provides better organization and display of commands and subcommands in
help output.

Example:
    ```python
    from tokeo.ext.argparse import Controller

    class MyController(Controller):
        class Meta:
            label = 'my_controller'
            help = 'My custom controller'

        @ex(help='My command help')
        def my_command(self):
            # Command implementation
            pass
    ```
"""

from cement.ext.ext_argparse import ArgparseController
import argparse


class TokeoHelpFormatter(argparse.HelpFormatter):
    """
    Custom help formatter for Tokeo CLI applications.

    Extends the standard argparse.HelpFormatter to provide improved formatting
    for subcommands with better organization and alignment.

    The formatter sorts subcommands alphabetically and improves indentation and
    spacing for better readability in complex command hierarchies.
    """

    def _iter_indented_subactions(self, action):
        """
        Iterate over subactions with proper indentation and sorting.

        Args:
            action: The argparse action containing subactions.

        Yields:
            Sorted subactions from the given action.
        """
        try:
            get_subactions = action._get_subactions
        except AttributeError:
            pass
        else:
            self._indent()
            if isinstance(action, argparse._SubParsersAction):
                for subaction in sorted(get_subactions(), key=lambda x: x.dest):
                    yield subaction
            else:
                for subaction in get_subactions():
                    yield subaction
            self._dedent()

    def _fill_text(self, text, width, indent):
        """
        Fill text with consistent indentation.

        Args:
            text: The text to format.
            width: The width to fill to.
            indent: The indentation string.

        Returns:
            Formatted text with proper indentation.
        """
        return ''.join(indent + line for line in text.splitlines(keepends=True)) + ' \n '

    def _format_action(self, action):
        """
        Format an action's help text with improved subcommand display.

        Args:
            action: The argparse action to format.

        Returns:
            Formatted help string for the action.
        """
        if isinstance(action, argparse._SubParsersAction):
            # inject new class variable for subcommand formatting
            subactions = action._get_subactions()
            invocations = [self._format_action_invocation(a) for a in subactions]
            self._subcommand_max_length = max(len(i) for i in invocations)

        if isinstance(action, argparse._SubParsersAction._ChoicesPseudoAction):
            # format subcommand help line
            subcommand = self._format_action_invocation(action)  # type: str
            width = self._subcommand_max_length
            help_text = ''
            if action.help:
                help_text = self._expand_help(action)
            return '  {:{width}}    {}\n'.format(subcommand, help_text, width=width)

        else:
            return super()._format_action(action)


class Controller(ArgparseController):
    """
    Base controller class for Tokeo CLI applications.

    Extends Cement's ArgparseController with an improved help formatter and
    default behavior for commands.

    Attributes:
        Meta: Configuration class for the controller.
    """

    class Meta:
        """Controller configuration settings."""

        argument_formatter = TokeoHelpFormatter

    def _default(self):
        """
        Default action when no subcommand is specified.

        Displays the help information for the controller.
        """
        self._parser.print_help()
