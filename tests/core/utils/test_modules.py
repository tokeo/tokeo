"""
Tests for tokeo.core.utils.modules: resolve_module and resolve_object load
what a config names, as module:attr or in the dotted form, and report every
failure as TokeoModulesLoadError with its cause; find_module_dir finds a
module without running it; override_module serves a local file in place of
an installed module, also one its parent package loads itself.
"""

import sys
import uuid

import pytest

from tokeo.core.exc import TokeoError
from tokeo.core.utils.modules import (
    TokeoModulesLoadError,
    find_module_dir,
    get_module_path,
    resolve_module,
    resolve_object,
)
from tokeo.core.utils.modules.override import TokeoModulesOverrideFinder, override_module


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    # a package of its own per test: its __init__ loads inner itself, like a
    # library does, broken fails on a missing dependency
    name = f'tokeo_tests_modules_{uuid.uuid4().hex[:8]}'
    folder = tmp_path / name
    folder.mkdir()
    (folder / '__init__.py').write_text('from . import inner  # noqa: F401\n')
    (folder / 'inner.py').write_text("VALUE = 'installed'\n\n\nclass Thing:\n    def method(self):\n        return 'method'\n")
    (folder / 'broken.py').write_text('import tokeo_tests_not_installed  # noqa: F401\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    yield name
    for module in [m for m in sys.modules if m == name or m.startswith(name + '.')]:
        del sys.modules[module]
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, TokeoModulesOverrideFinder)]


def test_load_error_is_a_tokeo_error():
    """TokeoModulesLoadError derives from TokeoError, main reports it cleanly."""
    assert issubclass(TokeoModulesLoadError, TokeoError)


# resolve_module


def test_resolve_module_imports_the_module(pkg):
    """resolve_module gives the imported module."""
    assert resolve_module(f'{pkg}.inner').VALUE == 'installed'


@pytest.mark.parametrize('name', [None, '', '  ', 42], ids=['none', 'empty', 'blank', 'int'])
def test_resolve_module_refuses_no_name(name):
    """A name that is no filled string raises TokeoModulesLoadError."""
    with pytest.raises(TokeoModulesLoadError, match='is no module name'):
        resolve_module(name)


def test_resolve_module_of_a_missing_module_raises(pkg):
    """A missing module raises TokeoModulesLoadError, the ImportError as cause."""
    with pytest.raises(TokeoModulesLoadError, match=f"module '{pkg}.nope' does not import") as caught:
        resolve_module(f'{pkg}.nope')
    assert isinstance(caught.value.__cause__, ImportError)


def test_resolve_module_names_the_import_error_inside_the_module(pkg):
    """An import error inside the module names the missing dependency."""
    with pytest.raises(TokeoModulesLoadError, match='tokeo_tests_not_installed'):
        resolve_module(f'{pkg}.broken')


# resolve_object


@pytest.mark.parametrize('form', ['{}.inner:VALUE', '{}.inner.VALUE'], ids=['colon', 'dotted'])
def test_resolve_object_takes_both_forms(pkg, form):
    """module:attr and the dotted form give the same object."""
    assert resolve_object(form.format(pkg)) == 'installed'


def test_resolve_object_takes_a_dotted_attr_after_the_colon(pkg):
    """After the colon the attr may be dotted, e.g. Class.method."""
    assert resolve_object(f'{pkg}.inner:Thing.method').__name__ == 'method'


@pytest.mark.parametrize('form', ['{}.inner:', ':VALUE', '{}'], ids=['no_attr', 'no_module', 'no_separator'])
def test_resolve_object_refuses_a_missing_part(pkg, form):
    """A name without module or attr part raises TokeoModulesLoadError."""
    with pytest.raises(TokeoModulesLoadError, match='must be set as module:attr'):
        resolve_object(form.format(pkg))


def test_resolve_object_refuses_no_name():
    """A name that is no filled string raises TokeoModulesLoadError."""
    with pytest.raises(TokeoModulesLoadError, match='is no name of module:attr'):
        resolve_object(None)


def test_resolve_object_of_a_missing_attr_raises(pkg):
    """A missing attr raises TokeoModulesLoadError, the AttributeError as cause."""
    with pytest.raises(TokeoModulesLoadError, match='could not be found') as caught:
        resolve_object(f'{pkg}.inner:nope')
    assert isinstance(caught.value.__cause__, AttributeError)


@pytest.mark.parametrize('form', ['{}.broken:run', '{}.broken.run'], ids=['colon', 'dotted'])
def test_resolve_object_does_not_hide_an_import_error_inside_the_module(pkg, form):
    """In both forms an import error inside the module names the dependency."""
    with pytest.raises(TokeoModulesLoadError, match='tokeo_tests_not_installed'):
        resolve_object(form.format(pkg))


# find_module_dir and get_module_path


def test_find_module_dir_finds_a_module_without_running_it(pkg, tmp_path):
    """find_module_dir finds the dir of a module that would fail to import."""
    assert find_module_dir(f'{pkg}.broken') == str(tmp_path / pkg)
    assert f'{pkg}.broken' not in sys.modules


@pytest.mark.parametrize('name', ['{}.nope', 'tokeo_tests_no_such_package.inner', ''], ids=['module', 'package', 'empty'])
def test_find_module_dir_of_a_missing_module_is_none(pkg, name):
    """A module that cannot be found gives None."""
    assert find_module_dir(name.format(pkg)) is None


def test_get_module_path_gives_the_dir_of_a_module_and_a_package(pkg):
    """get_module_path gives the dir of a plain module and of a package."""
    assert get_module_path(f'{pkg}.inner') == get_module_path(pkg)


# override_module


def _local(tmp_path, text):
    # a local file to override a module with
    path = tmp_path / 'local.py'
    path.write_text(text)
    return path


def test_override_module_serves_the_local_file(pkg, tmp_path):
    """The overridden module is the local file, also on the parent package."""
    module = override_module(f'{pkg}.inner', _local(tmp_path, "VALUE = 'local'\n"))
    assert module.VALUE == 'local'
    assert getattr(sys.modules[pkg], 'inner') is module


def test_override_module_works_for_a_module_its_package_loads_itself(pkg, tmp_path):
    """The __init__ of the package imports inner itself and gets the local file."""
    override_module(f'{pkg}.inner', _local(tmp_path, "VALUE = 'local'\n"))
    assert resolve_object(f'{pkg}:inner.VALUE') == 'local'


def test_override_module_refuses_an_imported_module(pkg, tmp_path):
    """A module imported already is refused, its users would keep the old one."""
    resolve_module(pkg)
    with pytest.raises(TokeoModulesLoadError, match='is imported already'):
        override_module(f'{pkg}.inner', _local(tmp_path, "VALUE = 'local'\n"))


def test_override_module_refuses_a_missing_file(pkg, tmp_path):
    """A missing file is refused and no finder is left behind."""
    with pytest.raises(TokeoModulesLoadError, match='does not exist'):
        override_module(f'{pkg}.inner', tmp_path / 'missing.py')
    assert not any(isinstance(f, TokeoModulesOverrideFinder) for f in sys.meta_path)


def test_override_module_failing_load_leaves_nothing_behind(pkg, tmp_path):
    """A local file that fails leaves neither the finder nor the module."""
    with pytest.raises(ZeroDivisionError):
        override_module(f'{pkg}.inner', _local(tmp_path, 'VALUE = 1 / 0\n'))
    assert f'{pkg}.inner' not in sys.modules
    assert not any(isinstance(f, TokeoModulesOverrideFinder) for f in sys.meta_path)
