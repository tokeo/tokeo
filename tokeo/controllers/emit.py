from tokeo.ext.argparse import Controller
from tokeo.core import tasks
from cement import ex


class EmitController(Controller):

    class Meta:
        label = 'emit'
        stacked_type = 'nested'
        stacked_on = 'base'

        # disable the ugly curly command doubled listening
        subparser_options = dict(metavar='')

        # text displayed at the top of --help output
        description = 'Emit tasks to the message queue.'

        # text displayed at the bottom of --help output
        epilog = 'Example: tokeo emit task --option --param value'

        # short help information
        help = 'emit tasks manually'

    def _default(self):
        """Default command action if no sub-command is passed."""

        self.app.args.print_help()

    @ex(
        help='emit the count-words task for an url',
        arguments=[
            (
                ['--url'],
                dict(
                    action='store',
                    required=True,
                    help='Url for the resource to get counted',
                ),
            ),
        ],
    )
    def count_words(self):
        self.app.log.info('Emit count-words task with url: ' + self.app.pargs.url)
        tasks.actors.count_words.send(self.app.pargs.url)
