"""
A tokeo app with the automate shell, run by test_shell in a pseudo terminal.
"""

import sys
from tests.ext.automate.automate_tests_helper import AutomateTest


class AutomateShellProbeTest(AutomateTest):

    class Meta:
        label = 'probe'


if __name__ == '__main__':
    with AutomateShellProbeTest(argv=sys.argv[1:]) as app:
        app.run()
