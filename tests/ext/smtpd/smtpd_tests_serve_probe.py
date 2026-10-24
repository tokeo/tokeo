"""
Runs one real Tokeo test app serving smtpd, for the process tests.

Started as its own process by test_smtpd.py: the termination guarantees
under test (exit code, port release, worker cleanup, SIGKILL behaviour) are
process properties and can only be observed from outside. Inside, a regular
``TokeoTest`` Cement app loads the extension and blocks in ``serve`` until a
signal ends it; the config section and the service selection arrive as json
arguments.
"""

import json
import sys
from tests.ext.smtpd.smtpd_tests_helper import SmtpdTest


class SmtpdServeProbeTest(SmtpdTest):
    """The test app of the probe, serving smtpd until a signal ends it."""


def main():
    config = json.loads(sys.argv[1])
    names = json.loads(sys.argv[2])
    with SmtpdServeProbeTest(config_defaults={'smtpd': config}) as app:
        app.smtpd.serve(names)


if __name__ == '__main__':
    main()
