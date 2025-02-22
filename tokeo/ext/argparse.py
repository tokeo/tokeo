from cement.ext.ext_argparse import ArgparseController
import argparse


class TokeoHelpFormatter(argparse.HelpFormatter):

    def _iter_indented_subactions(self, action):
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
        return ''.join(indent + line for line in text.splitlines(keepends=True)) + ' \n '

    def _format_action(self, action):
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

    class Meta:
        argument_formatter = TokeoHelpFormatter

    def _default(self):
        self._parser.print_help()
