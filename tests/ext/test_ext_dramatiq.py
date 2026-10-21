"""
Tests for tokeo.ext.dramatiq without a running broker.

The dramatiq cli is replaced by a recorder, so the tests check what the serve
command hands over to dramatiq and what it does before the workers start.
"""

import sys

import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest


class DramatiqTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.diskcache',
            'tokeo.ext.dramatiq',
        ]


def dramatiq_app(tmp, argv):
    d = init_defaults('diskcache')
    d['diskcache']['directory'] = tmp.dir
    return DramatiqTest(argv=argv, config_defaults=d)


@pytest.fixture
def serve(tmp, monkeypatch):
    """Run the serve command with a recording cli and purge."""
    # serve rewrites sys.argv, keep the one of pytest
    monkeypatch.setattr(sys, 'argv', ['tokeo'])
    calls = dict(main=[], purge=0, argv=None)

    def fake_main(args):
        calls['main'].append(args)
        return 0

    monkeypatch.setattr('tokeo.ext.dramatiq.cli.main', fake_main)

    def run(*options):
        with dramatiq_app(tmp, ['dramatiq', 'serve', *options]) as app:

            def fake_purge():
                calls['purge'] += 1
                return 0

            monkeypatch.setattr(app.dramatiq.locks, 'purge', fake_purge)
            app.run()
            calls['argv'] = list(sys.argv)
            calls['exit_code'] = app.exit_code
        return calls

    return run


def test_serve_purges_locks(serve):
    calls = serve()
    assert calls['purge'] == 1
    assert len(calls['main']) == 1
    assert calls['exit_code'] == 0


def test_serve_no_purge_keeps_locks(serve):
    calls = serve('--no-purge')
    assert calls['purge'] == 0
    assert len(calls['main']) == 1


def test_serve_no_purge_survives_watch_restart(serve):
    # a restart by --watch re-executes sys.argv, so the restored argv must
    # still carry --no-purge for the next start of the service
    calls = serve('--no-purge', '--watch', __file__)
    assert calls['argv'] == ['tokeo', 'dramatiq', 'serve', '--no-purge', '--watch', __file__]
    assert calls['main'][0].watch is not None
