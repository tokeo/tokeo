"""
Tests for the packages of the repository: tokeo, tokeo-base, tokeo-smtpd and
tokeo-pb carry the same version, set by ```make version```, tokeo-diskcache
the version of DiskCache it is taken from. tokeo-base, tokeo-smtpd, tokeo-pb
and tokeo-diskcache import only what their own package provides, so each one
works without the rest of tokeo, and tokeo-diskcache never imports pickle.
"""

import os
import ast
import sys
import tomllib

import pytest

from tokeo.core.version import get_version
from tests.utils import ROOT

# the imports a package may use besides the standard library
ALLOWED = {
    'tokeo-base': ('tokeo.core.exc', 'tokeo.core.utils'),
    'tokeo-smtpd': ('tokeo.core.exc', 'tokeo.core.utils', 'tokeo.core.smtpd', 'cryptography'),
    'tokeo-pb': ('tokeo.core.exc', 'tokeo.core.utils', 'tokeo.core.pocketbase', 'httpx'),
    'tokeo-diskcache': ('tokeo.core.diskcache',),
}

# the modules that can run code while they load data
UNSAFE = ('pickle', 'pickletools', 'marshal', 'shelve')


def _pyproject(package):
    # the parsed pyproject.toml of a package directory
    with open(os.path.join(ROOT, package, 'pyproject.toml'), 'rb') as f:
        return tomllib.load(f)


def _imports(package):
    # (file, module) of every absolute import in the sources of a package
    for folder, _, files in os.walk(os.path.join(ROOT, package, 'tokeo')):
        for name in files:
            if not name.endswith('.py'):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding='utf-8') as f:
                tree = ast.parse(f.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        yield path, alias.name
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    yield path, node.module


def _allowed(module, allowed):
    # a module of the standard library or of the allowed ones
    if module.split('.')[0] in sys.stdlib_module_names:
        return True
    return any(module == entry or module.startswith(entry + '.') for entry in allowed)


def test_all_packages_carry_the_version_of_tokeo():
    """tokeo-base, tokeo-smtpd and tokeo-pb have the version of tokeo."""
    version = get_version()
    assert _pyproject('tokeo-base')['project']['version'] == version
    assert _pyproject('tokeo-smtpd')['project']['version'] == version
    assert _pyproject('tokeo-pb')['project']['version'] == version


def test_all_packages_pin_each_other_by_the_version_of_tokeo():
    """tokeo-base is pinned by all, tokeo-smtpd and tokeo-pb by tokeo dev."""
    version = get_version()
    tokeo = _pyproject('.')['project']
    assert f'tokeo-base=={version}' in tokeo['dependencies']
    assert f'tokeo-smtpd=={version}' in tokeo['optional-dependencies']['dev']
    assert f'tokeo-pb=={version}' in tokeo['optional-dependencies']['dev']
    assert _pyproject('tokeo-smtpd')['project']['dependencies'] == [f'tokeo-base=={version}']
    assert f'tokeo-base=={version}' in _pyproject('tokeo-pb')['project']['dependencies']


def test_tokeo_pins_tokeo_diskcache_by_its_own_version():
    """tokeo dev requires tokeo-diskcache in the version of its pyproject."""
    version = _pyproject('tokeo-diskcache')['project']['version']
    assert f'tokeo-diskcache=={version}' in _pyproject('.')['project']['optional-dependencies']['dev']


@pytest.mark.parametrize('package', sorted(ALLOWED))
def test_a_package_imports_only_its_own_parts(package):
    """Besides the standard library a package imports only the allowed modules."""
    found = [f'{os.path.relpath(path, ROOT)}: {module}' for path, module in _imports(package) if not _allowed(module, ALLOWED[package])]
    assert found == []


def test_tokeo_diskcache_never_imports_pickle():
    """No module of tokeo-diskcache imports pickle or another unsafe loader."""
    found = [f'{os.path.relpath(path, ROOT)}: {module}' for path, module in _imports('tokeo-diskcache') if module.split('.')[0] in UNSAFE]
    assert found == []
