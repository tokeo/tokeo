from cement import ex  # noqa: F401
from cement.utils.version import get_version_banner
from tokeo.ext.argparse import Controller
from ..core.version import get_version

DESCRIPTION = """{{ app_description }}"""
VERSION_BANNER = f"""
{DESCRIPTION}
{{ app_name }} {get_version()}
{get_version_banner()}
"""


class BaseController(Controller):

    class Meta:
        label = 'base'

        # disable the ugly curly command doubled listening
        subparser_options = dict(metavar='')

        # text displayed at the top of --help output
        description = DESCRIPTION

        # text displayed at the bottom of --help output
        epilog = 'Example: {{ app_label }} command --option --param value'

        # short help is empty on base
        help = ''

        # controller level arguments. ex: '{{ app_label }} --version'
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
