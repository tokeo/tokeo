from tokeo.ext.argparse import Controller
from cement import ex
from cement.utils.version import get_version_banner
from ..core.version import get_version


VERSION_BANNER = f"""
The Tokeo CLI contains all tasks, jobs and management for your Event-Driven Backend.
Tokeo {get_version()}
{get_version_banner()}
"""


class BaseController(Controller):

    class Meta:
        label = 'base'

        # disable the ugly curly command doubled listening
        subparser_options = dict(metavar='')

        # text displayed at the top of --help output
        description = 'The Tokeo CLI contains all tasks, jobs and management for your Event-Driven Backend.'

        # text displayed at the bottom of --help output
        epilog = 'Example: tokeo command --option --param value'

        # short help is empty on base
        help = ''

        # controller level arguments. ex: 'tokeo --version'
        arguments = [
            # add a version banner
            (
                ['-v', '--version'],
                dict(
                    action='version',
                    version=VERSION_BANNER,
                ),
            ),
        ]
