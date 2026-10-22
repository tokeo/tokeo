"""
A tokeo app with the automate shell, run by test_ext_automate_shell in a
pseudo terminal.
"""

import sys
from tokeo.main import TokeoTest
from tests.ext.test_ext_automate import defaults


class ProbeApp(TokeoTest):

    class Meta:
        label = 'probe'
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
            'tokeo.ext.tui.output',
            'tokeo.ext.automate',
        ]


if __name__ == '__main__':
    with ProbeApp(argv=sys.argv[1:], config_defaults=defaults) as app:
        app.run()
