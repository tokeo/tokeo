import sys
import os
from cement import Controller, ex
from dramatiq import cli
from cedra.core import tasks


class Serve(Controller):

    class Meta:
        label = 'serve'
        stacked_type = 'nested'
        stacked_on = 'base'

        # text displayed at the top of --help output
        description = 'Run the dramatiq service.'

        # text displayed at the bottom of --help output
        epilog = 'Example: cedra serve --option --param value'

        # apply arguments to the _default method as well
        arguments = [
            (
                ['--watch'],
                dict(
                    action='store_true',
                    help='Reload actors on changes and restart workers',
                ),
            ),
        ]

    def _default(self):
        self.app.log.info('Spinning up the damatiq workers ...')
        # prepare a sys.argv array to contorl the dramatiq main instance
        # initialize with "this" script (should by cedra)
        sys.argv = [sys.argv[0]]
        # append some worker settings
        sys.argv.extend(
            ['--processes', str(self.app.config.get('worker', 'processes'))],
        )
        sys.argv.extend(
            ['--threads', str(self.app.config.get('worker', 'threads'))],
        )
        # check for watch parameter
        if self.app.pargs.watch:
            # add watcher for the module path of tasks
            sys.argv.extend(
                ['--watch', os.path.dirname(os.path.abspath(tasks.__file__))],
            )
        # add the broker and actors
        sys.argv.extend(
            ['cedra.core.dramatiq.broker:setup', 'cedra.core.tasks.actors'],
        )
        # parse sys.argv as dramatiq command line options
        args = cli.make_argument_parser().parse_args()
        # restore the sys.argv content for later restart etc. from inside dramatiq
        sys.argv = [sys.argv[0]] + self.app.argv
        # go and run dramatiq workers with the parsed args
        sys.exit(cli.main(args))
