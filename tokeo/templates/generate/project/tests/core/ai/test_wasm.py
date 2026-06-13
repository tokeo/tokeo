"""
Wasm sandbox tests for the {{ app_name }} project's two python-exec tools.

Proves that both built-in exec tools run through the wasm sandbox from inside a
generated project: the untrusted tool runs model-style code with total
isolation (no app mount), and the trusted tool is rebuilt in the guest from the
mounted project source. The enforcement mechanics (hard memory cap) are shown
with a tiny WAT guest so they run anywhere wasmtime is installed; the real
guest runs need a user-supplied CPython-WASI build and skip without one -- set
```TOKEO_TEST_PYTHON_WASM``` and ```TOKEO_TEST_WASI_STDLIB``` to run them.
"""

import os

import pytest
from cement.utils.misc import init_defaults
from tokeo.core.ai import ToolResult, TokeoAiError
from tokeo.core.ai.tools.python_untrusted_exec import TokeoAiPythonUntrustedExecTool
from tokeo.core.ai.tools.python_trusted_exec import TokeoAiPythonTrustedExecTool
from {{ app_label }}.main import {{ app_class_name }}Test

wasmtime = pytest.importorskip('wasmtime')


# a guest that grows memory forever, trapping once the store limit refuses --
# proves the hard cap without needing a real interpreter
_GROW_WAT = r"""(module (memory (export "memory") 1)
  (func (export "_start")
    (loop $g (if (i32.eq (memory.grow (i32.const 1)) (i32.const -1))
      (then unreachable)) (br $g))))"""


def test_{{ app_label }}_wasm_memory_cap_is_hard():
    # the store limit makes the guest trap instead of eating host memory
    engine = wasmtime.Engine()
    store = wasmtime.Store(engine)
    store.set_limits(memory_size=8 * 1024 * 1024)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    store.set_wasi(wasmtime.WasiConfig())
    instance = linker.instantiate(store, wasmtime.Module(engine, _GROW_WAT))
    with pytest.raises(wasmtime.Trap):
        instance.exports(store)['_start'](store)


def test_{{ app_label }}_untrusted_exec_runs_in_process():
    # the tool logic (compile/exec, result -> text) is sandbox-agnostic
    tool = TokeoAiPythonUntrustedExecTool(None)
    result = tool.exec(code='result = sum(range(10))')
    assert result.text == '45' and result.data == 45


def test_{{ app_label }}_trusted_exec_runs_in_process():
    tool = TokeoAiPythonTrustedExecTool(None)
    result = tool.exec(code='result = 6 * 7')
    assert result.text == '42'


# --------------------------------------------------------------------------------------
# end-to-end through the wasm sandbox, config embedded so the test is self-contained
# --------------------------------------------------------------------------------------

_PYTHON_WASM = os.environ.get('TOKEO_TEST_PYTHON_WASM')
_WASI_STDLIB = os.environ.get('TOKEO_TEST_WASI_STDLIB')

# default to the documented relative spot; override via env
_RUNTIME = _PYTHON_WASM or os.path.abspath('./wasm/python.wasm')
_STDLIB = _WASI_STDLIB or os.path.abspath('./wasm/lib/python3.13')

# the trusted tool is rebuilt in the guest from its dotted path
# (```tokeo.core.ai.tools...```), so the guest needs the tokeo source, the app
# source, AND the dependencies the tool base class pulls in (cement). these may
# live in three different trees (e.g. tokeo as a sibling checkout, the project
# here, cement in site-packages); resolve each from its installed package so
# the test works on any layout
import cement  # noqa: E402
import tokeo  # noqa: E402
import {{ app_label }}  # noqa: E402

_TOKEO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(tokeo.__file__)))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath({{ app_label }}.__file__)))
_DEPS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(cement.__file__)))

_have_build = os.path.exists(_RUNTIME) and os.path.isdir(_STDLIB)


class {{ app_class_name }}WasmTestApp({{ app_class_name }}Test):

    class Meta:
        # set framework extensions
        extensions = [
            'colorlog',
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.ai',
        ]


def _trusted_mounts():
    # mount tokeo, the app, and the deps tree, each at its own guest path, but
    # dedup roots that coincide (e.g. an editable install where two live in the
    # same tree) so wasm never gets the same host path twice
    roots = [('/tokeo', _TOKEO_ROOT), ('/app', _PROJECT_ROOT), ('/deps', _DEPS_ROOT)]
    mounts = {}
    seen = {}
    pythonpath = []
    for guest, host in roots:
        if host in seen:
            pythonpath.append(seen[host])
            continue
        seen[host] = guest
        mounts[guest] = host
        pythonpath.append(guest)
    return mounts, ':'.join(dict.fromkeys(pythonpath))


def wasm_ai_config():
    # both built-in exec tools, each behind a wasm sandbox: the untrusted tool
    # runs directly in the guest (no mount), the trusted tool is rebuilt there
    # from the mounted tokeo + project + dependency trees
    trusted_mounts, trusted_pythonpath = _trusted_mounts()
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        defaults=dict(profile='mock', agent=None),
        tools=dict(
            run_untrusted=dict(type='python_untrusted_exec'),
            run_trusted=dict(type='python_trusted_exec'),
        ),
        sandboxes=dict(
            wasm_untrusted=dict(
                type='wasm',
                tools=['run_untrusted'],
                options=dict(runtime=_RUNTIME, stdlib=_STDLIB, memory_mb=256, timeout=10),
            ),
            wasm_trusted=dict(
                type='wasm',
                tools=['run_trusted'],
                options=dict(
                    runtime=_RUNTIME,
                    stdlib=_STDLIB,
                    memory_mb=256,
                    timeout=10,
                    mounts=trusted_mounts,
                    env=dict(PYTHONPATH=trusted_pythonpath),
                ),
            ),
        ),
        agents=dict(
            untrusted_coder=dict(type='fundi', options=dict(sandboxes=['wasm_untrusted'])),
            trusted_coder=dict(type='fundi', options=dict(sandboxes=['wasm_trusted'])),
        ),
        profiles=dict(
            mock=dict(type='mock', agent='untrusted_coder'),
        ),
    )
    return cfg


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_{{ app_label }}_untrusted_exec_in_wasm_guest():
    # the untrusted tool runs the snippet directly in the guest (no app mount)
    # and the result crosses the file bridge back
    with {{ app_class_name }}WasmTestApp(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('untrusted_coder')
        out = app.ai._exec_in_sandbox(
            'run_untrusted',
            dict(code='import statistics\nresult = statistics.median([5, 1, 9, 3, 7])'),
            agent,
        )
        text = out.text if isinstance(out, ToolResult) else str(out)
        assert text == '5'


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_{{ app_label }}_trusted_exec_in_wasm_guest():
    # the trusted tool is rebuilt in the guest from the mounted project source;
    # importing the app package confirms it is reachable there
    with {{ app_class_name }}WasmTestApp(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('trusted_coder')
        out = app.ai._exec_in_sandbox(
            'run_trusted',
            dict(code='import {{ app_label }}\nresult = bool({{ app_label }}.__name__)'),
            agent,
        )
        text = out.text if isinstance(out, ToolResult) else str(out)
        assert text == 'True'


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_{{ app_label }}_untrusted_guest_cannot_import_the_app():
    # the untrusted path mounts no app: importing the project must fail in the
    # guest, proving the framework stays invisible to untrusted code
    with {{ app_class_name }}WasmTestApp(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('untrusted_coder')
        with pytest.raises(TokeoAiError, match='ModuleNotFoundError'):
            app.ai._exec_in_sandbox(
                'run_untrusted',
                dict(code='import {{ app_label }}\nresult = 1'),
                agent,
            )
