"""
Tests for tokeo.ext.nicegui without a running web server: the config and its
checks, the routes, apis and openapi docs set up by startup, the settings
handed to ```ui.run```, the hotload by watchdog, the serve command, the ux
helper and the pdoc hook.

```ui.run``` is recorded instead of run, except for the page tests: they
start nicegui in its user simulation, as ```nicegui.testing.user_simulation```
does, but around ```app.nicegui.startup()```, which calls ```ui.run``` itself,
and drive the page by the ```User``` of ```nicegui.testing``` through an ASGI
client, without a browser. Every test starts and ends with the global state
of nicegui reset.
"""

import os
import ast
import sys
import time
import types
import asyncio

import httpx
import pytest
from cement.utils.misc import init_defaults
from fastapi import FastAPI
from nicegui import core, ui, app as nicegui_app
from nicegui.client import Client
from nicegui.testing import User
from nicegui.testing.general import nicegui_reset_globals
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent, FileOpenedEvent, FileClosedNoWriteEvent
from tokeo.main import TokeoTest
from tokeo.ext.nicegui import (
    NiceguiElementHelper,
    TokeoNiceguiError,
    TokeoNiceguiWatchdogEventHandler,
    tokeo_nicegui_pdoc_render_decorator,
)
from tests.utils import wait_until

#: The routes of the tests, _pages of this module
ROUTES = dict(routes_module=__name__, routes='_pages')


class NiceguiTest(TokeoTest):
    """A test app with the nicegui extension and the given nicegui settings."""

    class Meta:
        extensions = ['tokeo.ext.print', 'tokeo.ext.nicegui']

    def __init__(self, argv=None, **nicegui):
        config = init_defaults('nicegui')
        config['nicegui'].update(nicegui)
        super().__init__(argv=argv or [], config_defaults=config)


class NiceguiPdocTest(NiceguiTest):
    """A test app with the nicegui and the pdoc extension."""

    class Meta:
        extensions = ['tokeo.ext.print', 'tokeo.ext.pdoc', 'tokeo.ext.nicegui']


def _pages():
    # the route handler of the tests: a page with the ux helper and an api
    @ui.page('/')
    def index():
        ui.label('Hello tokeo')
        NiceguiElementHelper().article('An article by ux')

    @nicegui_app.get('/api/ping')
    def ping():
        return dict(pong=True)


def _module(monkeypatch, name, **attributes):
    # a module the config can name, importable during the test only
    module = types.ModuleType(name)
    vars(module).update(attributes)
    monkeypatch.setitem(sys.modules, name, module)
    return name


def _paths():
    # the paths the fastapi app of nicegui serves
    return {getattr(route, 'path', None) for route in nicegui_app.routes}


def _simulate(app, visit):
    # nicegui's user simulation around startup: ui.run only prepares the
    # app, the lifespan starts it and an ASGI client asks it
    async def go():
        app.nicegui.startup()
        async with core.app.router.lifespan_context(core.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(core.app), base_url='http://test') as client:
                await visit(User(client), client)

    asyncio.run(go())


@pytest.fixture(autouse=True)
def nicegui_globals():
    """The global state of nicegui, reset before and after the test."""
    with nicegui_reset_globals():
        yield
    # routes, handlers and clients of this test do not reach the next one
    with nicegui_reset_globals():
        pass


@pytest.fixture(autouse=True)
def no_process_replacement(monkeypatch):
    """The calls of os.execv by hotload, recorded instead of replacing pytest."""
    replaced = []
    monkeypatch.setattr('tokeo.ext.nicegui.os_execv', lambda path, argv: replaced.append((path, argv)))
    monkeypatch.setattr('multiprocessing.util._exit_function', lambda: replaced.append('exit_function'))
    return replaced


@pytest.fixture
def runs(monkeypatch):
    """The settings of each call of ui.run, recorded instead of serving."""
    calls = []
    monkeypatch.setattr(ui, 'run', lambda **settings: calls.append(settings))
    return calls


@pytest.fixture
def simulation(monkeypatch):
    """The user simulation of nicegui, ui.run serves nothing then."""
    monkeypatch.setenv('NICEGUI_USER_SIMULATION', 'true')
    # the User of nicegui puts its own navigate, notify and download into ui
    for name in ('navigate', 'notify', 'download'):
        monkeypatch.setattr(ui, name, getattr(ui, name))


# setup


def test_setup_gives_ui_fastapi_app_and_ux():
    """app.nicegui gives the ui of nicegui, its fastapi app and the ux helper."""
    with NiceguiTest() as app:
        assert app.nicegui.ui is ui and app.nicegui.fastapi_app is nicegui_app
        assert isinstance(app.nicegui.ux, NiceguiElementHelper)


def test_setup_fills_the_defaults_and_keeps_given_settings():
    """A given port stays, the other settings get their defaults."""
    with NiceguiTest(port='8080') as app:
        settings = app.config.get_section_dict('nicegui')
    assert (settings['port'], settings['host'], settings['title']) == ('8080', '127.0.0.1', 'Tokeo NiceGUI')
    assert (settings['hotload_includes'], settings['routes'], settings['storage_secret']) == ('*.py', None, None)


@pytest.mark.parametrize(
    'settings, message',
    [
        ({}, 'Tokeo NiceGUI ready to go on'),
        (dict(title='Shop'), 'Shop ready to go on'),
        (dict(title='Shop', welcome_message='Up and running'), 'Up and running'),
    ],
    ids=['default', 'title', 'own'],
)
def test_setup_welcome_message(settings, message):
    """Without welcome_message the title makes the message."""
    with NiceguiTest(**settings) as app:
        assert app.nicegui._welcome_message == message


# startup


@pytest.mark.parametrize('hotload', [False, True])
@pytest.mark.parametrize('routes', [5, ['index'], dict(path='/')], ids=['int', 'list', 'dict'])
def test_startup_refuses_routes_other_than_a_name(runs, routes, hotload):
    """routes other than null or a name raises, also with hotload."""
    with NiceguiTest(routes_module=__name__, routes=routes) as app:
        with pytest.raises(TokeoNiceguiError, match='Misconfiguration for route handler. Must be null or name of method'):
            app.nicegui.startup(hotload=hotload)
        assert app.nicegui._watchdog_observer is None
    assert runs == []


@pytest.mark.parametrize('routes_module', [__name__, None], ids=['module', 'no_module'])
def test_startup_route_handler_missing(runs, routes_module):
    """A routes name missing in the routes module raises and serves nothing."""
    with NiceguiTest(routes_module=routes_module, routes='_no_pages') as app:
        with pytest.raises(TokeoNiceguiError, match=f'Route handler "_no_pages" could not be found in module "{routes_module}"'):
            app.nicegui.startup()
    assert runs == []


@pytest.mark.parametrize('key', ['routes_module', 'apis_module'])
def test_startup_module_not_importable(runs, key):
    """A routes or apis module that does not import raises its ImportError."""
    with NiceguiTest(**{key: 'no.such.module'}) as app:
        with pytest.raises(ModuleNotFoundError, match="No module named 'no'"):
            app.nicegui.startup()
    assert runs == []


def test_startup_with_hotload_survives_a_failed_load(runs, tmp_path, monkeypatch):
    """With hotload a missing route handler is logged, watched and served anyway."""
    with NiceguiTest(routes_module=__name__, routes='_no_pages') as app:
        errors = []
        monkeypatch.setattr(app.log, 'error', lambda msg, **kw: errors.append(msg))
        app.nicegui.startup(hotload=True, hotload_dir=str(tmp_path))
        observer = app.nicegui._watchdog_observer
        assert observer.is_alive() and len(runs) == 1
    assert errors == [
        'Loading apis and routes failed: Route handler "_no_pages" could not be found in module "tests.ext.nicegui.test_nicegui"'
    ]
    # the close of the app stops the watchdog
    assert not observer.is_alive()


def test_startup_registers_the_pages_and_apis_of_the_route_handler(runs):
    """The route handler adds the page / and the api /api/ping before ui.run."""
    with NiceguiTest(**ROUTES) as app:
        app.nicegui.startup()
    assert '/' in Client.page_routes.values() and '/api/ping' in _paths()
    assert len(runs) == 1


def test_startup_runs_nicegui_with_the_config(runs):
    """ui.run gets the config as numbers where needed, never shows or reloads."""
    settings = dict(host='0.0.0.0', port='5000', binding_refresh_interval='0.2', message_history_length='10', storage_secret='s3cret')
    with NiceguiTest(**settings) as app:
        app.nicegui.startup()
    (run,) = runs
    assert (run['host'], run['port'], run['binding_refresh_interval'], run['message_history_length']) == ('0.0.0.0', 5000, 0.2, 10)
    assert (run['title'], run['storage_secret'], run['reconnect_timeout'], run['uvicorn_logging_level']) == (
        'Tokeo NiceGUI',
        's3cret',
        5.0,
        'warning',
    )
    # what tokeo fixes, the auto reload of uvicorn included
    fixed = ('show', 'native', 'reload', 'fastapi_docs', 'show_welcome_message')
    assert [run[key] for key in fixed] == [False] * 5 and run['uvicorn_reload_dirs'] is None


@pytest.mark.parametrize(
    'run_kwargs, port', [(dict(port=9000, ssl_keyfile='key.pem'), 9000), ('port=9000', 4123)], ids=['dict', 'not_a_dict']
)
def test_startup_run_kwargs_win_over_the_config(runs, run_kwargs, port):
    """run_kwargs as dict replace and add settings of ui.run, else not."""
    with NiceguiTest(run_kwargs=run_kwargs) as app:
        app.nicegui.startup()
    (run,) = runs
    assert run['port'] == port and ('ssl_keyfile' in run) is isinstance(run_kwargs, dict)


def test_startup_welcome_message_logged_when_nicegui_starts(simulation, monkeypatch):
    """The welcome message is logged once nicegui has started."""
    with NiceguiTest(welcome_message='Shop is up at') as app:
        infos = []
        monkeypatch.setattr(app.log, 'info', lambda msg, **kw: infos.append(msg))

        async def visit(user, client):
            assert any(info.startswith('Shop is up at') for info in infos)

        _simulate(app, visit)


# openapi


@pytest.fixture
def openapi(monkeypatch):
    """The openapi settings of the fastapi app of nicegui, startup sets them."""
    monkeypatch.setattr(nicegui_app, 'openapi_url', nicegui_app.openapi_url)
    monkeypatch.setattr(nicegui_app, 'openapi', nicegui_app.openapi)
    return dict(openapi_documentation='page', openapi_url='/openapi.json', openapi_swagger_url='/docs', openapi_redoc_url='/redoc')


def test_startup_openapi_with_custom_module(runs, openapi, monkeypatch):
    """A custom module gives the openapi schema and the swagger and redoc pages."""
    module = _module(
        monkeypatch,
        'tokeo_tests_openapi',
        get_openapi_custom=lambda: dict(openapi='3.1.0', info=dict(title='Shop', version='1')),
        custom_swagger_ui_html=lambda: 'swagger',
        custom_redoc_html=lambda: 'redoc',
    )
    with NiceguiTest(openapi_custom_module=module, **openapi) as app:
        app.nicegui.startup()
    assert nicegui_app.openapi() == dict(openapi='3.1.0', info=dict(title='Shop', version='1'))
    assert {'/openapi.json', '/docs', '/redoc'} <= _paths()
    assert runs[0]['endpoint_documentation'] == 'page'


def test_startup_openapi_without_custom_module_keeps_fastapi(runs, openapi, monkeypatch):
    """Without a custom module fastapi serves its schema, no swagger or redoc."""
    with NiceguiTest(**openapi) as app:
        warnings = []
        monkeypatch.setattr(app.log, 'warning', lambda msg, **kw: warnings.append(msg))
        app.nicegui.startup()
    assert nicegui_app.openapi.__func__ is FastAPI.openapi
    assert '/openapi.json' in _paths() and not {'/docs', '/redoc'} & _paths()
    # the missing module is logged as failed customizing
    assert warnings == [
        "Could not start the custom openapi interface and documentation: 'NoneType' object has no attribute 'get_openapi_custom'"
    ]


@pytest.mark.parametrize('documentation, url', [(None, '/openapi.json'), ('page', None)], ids=['no_documentation', 'no_url'])
def test_startup_openapi_needs_documentation_and_url(runs, openapi, documentation, url):
    """Without openapi_documentation or openapi_url no schema is served."""
    with NiceguiTest(openapi_documentation=documentation, openapi_url=url) as app:
        app.nicegui.startup()
    assert nicegui_app.openapi_url is None and '/openapi.json' not in _paths()


# hotload


def test_startup_hotload_dir_switch_wins_over_config(runs, tmp_path):
    """The hotload_dir of the switch wins over the one of the config."""
    (tmp_path / 'config').mkdir()
    (tmp_path / 'switch').mkdir()
    with NiceguiTest(hotload_dir=str(tmp_path / 'config')) as app:
        app.nicegui.startup(hotload=True, hotload_dir=str(tmp_path / 'switch'))
        assert app.nicegui._hotload_dir == str(tmp_path / 'switch')
        app.nicegui.shutdown()
        app.nicegui.startup(hotload=True)
        assert app.nicegui._hotload_dir == str(tmp_path / 'config')


def test_startup_hotload_dir_defaults_to_the_routes_module(runs):
    """Without hotload_dir the folder of the routes module is watched."""
    with NiceguiTest(**ROUTES) as app:
        app.nicegui.startup(hotload=True)
        assert app.nicegui._hotload_dir == os.path.dirname(__file__)


def test_startup_hotload_dir_defaults_to_main_dir(runs):
    """Without hotload_dir and modules the main dir of the app is watched."""
    with NiceguiTest() as app:
        app.nicegui.startup(hotload=True)
        assert app.nicegui._hotload_dir == app._meta.main_dir


def test_hotload_restarts_the_process_after_a_change(runs, tmp_path, no_process_replacement):
    """A changed .py file makes the close of the app replace the process."""
    with NiceguiTest() as app:
        app.nicegui.startup(hotload=True, hotload_dir=str(tmp_path))
        (tmp_path / 'page.py').write_text('changed = True\n')
        assert wait_until(lambda: app.nicegui._watchdog_hotload_requested)
    assert no_process_replacement == ['exit_function', (sys.executable, [sys.executable] + sys.argv)]


def test_hotload_without_change_keeps_the_process(runs, tmp_path, no_process_replacement):
    """Without a change the close of the app replaces nothing."""
    with NiceguiTest() as app:
        app.nicegui.startup(hotload=True, hotload_dir=str(tmp_path))
    assert no_process_replacement == []


@pytest.mark.parametrize(
    'event, reported',
    [
        (FileModifiedEvent('/app/page.py'), True),
        (FileCreatedEvent('/app/PAGE.PY'), True),
        (FileMovedEvent('/app/page.tmp', '/app/page.py'), True),
        (FileOpenedEvent('/app/page.py'), False),
        (FileClosedNoWriteEvent('/app/page.py'), False),
        (FileModifiedEvent('/app/.page.py'), False),
        (FileModifiedEvent('/app/~page.py'), False),
        (FileModifiedEvent('/app/notes.txt'), False),
    ],
    ids=['modified', 'created_upper_case', 'moved', 'opened', 'closed', 'hidden', 'backup', 'other_file'],
)
def test_watchdog_handler_reports_changes_of_included_files(event, reported):
    """Only a real change of an included, not excluded file calls back."""
    calls = []
    handler = TokeoNiceguiWatchdogEventHandler(patterns=['*.py'], ignore_patterns=['.*', '~*'], callback=lambda: calls.append(1))
    handler.dispatch(event)
    assert calls == ([1] if reported else [])


def test_hotload_loop_shuts_nicegui_down_after_the_settle_time(monkeypatch):
    """A change shuts nicegui down once 0.2 s of hotload_settle passed."""
    shutdowns = []
    monkeypatch.setattr(nicegui_app, 'shutdown', lambda: shutdowns.append(time.monotonic()))
    with NiceguiTest(hotload_interval=0.01, hotload_settle=0.2) as app:
        app.nicegui._watchdog_on_event()
        changed = time.monotonic()
        asyncio.run(app.nicegui._watchdog_file_changes())
    (shutdown,) = shutdowns
    assert shutdown - changed >= 0.2


# serve


def test_serve_starts_nicegui(runs):
    """nicegui serve runs nicegui once without hotload."""
    with NiceguiTest(['nicegui', 'serve']) as app:
        app.run()
        assert app.nicegui._watchdog_observer is None
    assert len(runs) == 1


def test_serve_hotload_switches(runs, tmp_path):
    """nicegui serve --hotload --hotload-dir watches the given folder."""
    with NiceguiTest(['nicegui', 'serve', '--hotload', '--hotload-dir', str(tmp_path)]) as app:
        app.run()
        assert app.nicegui._hotload_dir == str(tmp_path) and app.nicegui._watchdog_observer.is_alive()
    assert len(runs) == 1


# pages


def test_page_of_the_route_handler_shows_label_and_ux_element(simulation):
    """The page / shows its label and the article made by the ux helper."""
    with NiceguiTest(**ROUTES) as app:

        async def visit(user, client):
            await user.open('/')
            await user.should_see('Hello tokeo')
            await user.should_see('An article by ux')

        _simulate(app, visit)


def test_api_of_the_route_handler_answers(simulation):
    """The api /api/ping of the route handler answers with its json."""
    with NiceguiTest(**ROUTES) as app:

        async def visit(user, client):
            response = await client.get('/api/ping')
            assert (response.status_code, response.json()) == (200, dict(pong=True))

        _simulate(app, visit)


# ux


def test_ux_outside_a_page_while_nicegui_runs_raises(simulation):
    """The ux helper refuses an element outside of a page while nicegui runs."""
    with NiceguiTest() as app:

        async def visit(user, client):
            with pytest.raises(TokeoNiceguiError, match='No active NiceGUI client context found'):
                app.nicegui.ux.article('outside')

        _simulate(app, visit)


@pytest.mark.xfail(
    strict=True,
    reason='guard_user_context checks for the shared client of NiceGUI 2; NiceGUI 3 makes a script client '
    'for UI in the global scope, so the guard passes (tokeo/ext/nicegui.py:174-192)',
)
def test_ux_in_the_global_scope_before_nicegui_runs_raises():
    """The ux helper refuses an element in the global scope before ui.run."""
    with NiceguiTest() as app:
        with pytest.raises(TokeoNiceguiError, match='global scope'):
            app.nicegui.ux.article('global')


# pdoc


@pytest.mark.parametrize(
    'source, decorator, params',
    [
        ("@ui.page('/about')", '@ui.page', '"/about"'),
        ("@app.nicegui.ui.page('/')", '@app.nicegui.ui.page', '"/"'),
        ("@fa.get('/api/ping')", '@fa.get', '"/api/ping"'),
        ('@app.nicegui.fastapi_app.post(42)', '@app.nicegui.fastapi_app.post', '42'),
        ('@ui.page(path)', '@ui.page', '...path...'),
    ],
    ids=['ui_page', 'app_page', 'fa_get', 'number', 'name'],
)
def test_pdoc_render_decorator_shows_path_and_docstring(source, decorator, params):
    """The pdoc hook shows the path of a page or api decorator and its docstring."""
    (call,) = ast.parse(f'{source}\ndef f(): pass').body[0].decorator_list
    with NiceguiPdocTest() as app:
        rendered = tokeo_nicegui_pdoc_render_decorator(app, None, decorator, call.args, call.keywords)
        key = 'nicegui.page' if 'page' in decorator else 'fastapi.' + decorator.rsplit('.', 1)[1]
        assert rendered == dict(decorator=decorator, params=params, docstring=app.pdoc.docstrings('decorator', key))
    assert rendered['docstring']


def test_pdoc_render_decorator_leaves_others_alone():
    """Another decorator is left to the other hooks, without args no params."""
    with NiceguiPdocTest() as app:
        assert tokeo_nicegui_pdoc_render_decorator(app, None, '@app.route', [], {}) is None
        assert tokeo_nicegui_pdoc_render_decorator(app, None, '@ui.page', None, None)['params'] is None
