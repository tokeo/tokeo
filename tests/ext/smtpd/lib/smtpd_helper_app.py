"""
Runs one real Tokeo test app serving smtpd, for the process tests.

Started as a subprocess by test_ext_smtpd.py: the termination guarantees
under test (exit code, port release, worker cleanup, SIGKILL behaviour) are
process properties and can only be observed from outside. Inside, a regular
``TokeoTest`` Cement app loads the extension and blocks in ``serve`` until a
signal ends it; the config section and the service selection arrive as json
arguments.
"""

import json
import sys


def main():
    config = json.loads(sys.argv[1])
    names = json.loads(sys.argv[2])
    from tokeo.main import TokeoTest

    class SmtpdServeApp(TokeoTest):

        class Meta:
            extensions = [
                'tokeo.ext.yaml',
                'tokeo.ext.print',
                'tokeo.ext.smtpd',
            ]

    with SmtpdServeApp(config_defaults={'smtpd': config}) as app:
        app.smtpd.serve(names)


if __name__ == '__main__':
    main()
