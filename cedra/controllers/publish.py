from cement import Controller, ex
from cedra.core import tasks


class Publish(Controller):

    class Meta:
        label = 'publish'
        stacked_type = 'nested'
        stacked_on = 'base'

        # text displayed at the top of --help output
        description = 'Publish tasks to the message queue.'

        # text displayed at the bottom of --help output
        epilog = 'Example: cedra publish task --option --param value'

    def _default(self):
        """Default command action if no sub-command is passed."""

        self.app.args.print_help()


    @ex(
        help='publish an url to the count_words task',
        arguments = [
            (
                ['--url'],
                dict(
                    action='store',
                    required=True,
                    help='Link to the resource get\'s counted',
                ),
            ),
        ]
    )
    def count_words(self):
        self.app.log.info('Publish task and paramters ...')
        tasks.actors.count_words.send(self.app.pargs.url)
