from cement import Controller, ex
from cement.utils.version import get_version_banner
from ..core.version import get_version

VERSION_BANNER = """
The Cedra CLI contains all the dramatiq workers and publishers. %s
%s
""" % (
    get_version(),
    get_version_banner(),
)


class Base(Controller):

    class Meta:
        label = 'base'

        # text displayed at the top of --help output
        description = 'The Cedra CLI contains all the dramatiq workers and publishers.'

        # text displayed at the bottom of --help output
        epilog = 'Example: cedra command --option --param value'

        # controller level arguments. ex: 'cedra --version'
        arguments = [
            ### add a version banner
            (
                ['-v', '--version'],
                dict(
                    action='version',
                    version=VERSION_BANNER,
                ),
            ),
        ]

    def _default(self):
        """Default application action if no sub-command is passed."""

        self.app.args.print_help()
