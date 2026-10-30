"""
Tests for tokeo.core.diskcache: the cache keeps raw keys and values like
DiskCache and stores the rest as JSON, never loads pickled data, also when it
is written into the database or a value file by hand, and creates its
directories only for the owner.
"""

import os
import stat
import pickle
import sqlite3

import pytest

from tokeo.core.diskcache import Cache, Lock
from tokeo.core.diskcache.core import MODE_PICKLE


class _Marker:
    """A pickle that creates a directory when it is loaded."""

    def __init__(self, path):
        self.path = path

    def __reduce__(self):
        return (os.mkdir, (self.path,))


@pytest.fixture
def cache(tmp_path):
    """A cache in its own directory, values above 64 bytes go to files."""
    with Cache(str(tmp_path / 'cache'), disk_min_file_size=64) as cache:
        yield cache


@pytest.mark.parametrize('value', ['text', b'bytes', 42, 1.5, None, True, [1, 'a'], {'a': [1, 2]}, {'big': 'x' * 100}])
def test_values_come_back_as_stored(cache, value):
    """Raw values and JSON values, also from a value file, come back equal."""
    cache.set('key', value)
    assert cache.get('key') == value


def test_a_tuple_comes_back_as_list(cache):
    """JSON knows no tuple, a tuple comes back as list."""
    cache.set('key', (1, 2))
    assert cache.get('key') == [1, 2]


def test_a_value_that_is_no_json_is_refused(cache):
    """An object JSON cannot encode raises a TypeError on set."""
    with pytest.raises(TypeError):
        cache.set('key', object())


def test_a_key_that_is_no_raw_type_works_as_json(cache):
    """A tuple key is found again and listed as list."""
    cache.set(('a', 1), 'value')
    assert cache.get(('a', 1)) == 'value'
    assert list(cache) == [['a', 1]]


def test_a_pickled_value_is_never_loaded(cache, tmp_path):
    """A pickle written into the database raises a ValueError and runs nothing."""
    marker = str(tmp_path / 'marker')
    cache.set('key', [1])
    cache._sql('UPDATE Cache SET mode = ?, value = ? WHERE key = ?', (MODE_PICKLE, sqlite3.Binary(pickle.dumps(_Marker(marker))), 'key'))
    with pytest.raises(ValueError, match='pickled value is not read'):
        cache.get('key')
    assert not os.path.exists(marker)


def test_a_pickled_value_file_is_never_loaded(cache, tmp_path):
    """A pickle written into a value file raises a ValueError and runs nothing."""
    marker = str(tmp_path / 'marker')
    cache.set('key', {'big': 'x' * 100})
    ((filename,),) = cache._sql('SELECT filename FROM Cache WHERE key = ?', ('key',)).fetchall()
    with open(os.path.join(cache.directory, filename), 'wb') as f:
        f.write(pickle.dumps(_Marker(marker)))
    cache._sql('UPDATE Cache SET mode = ? WHERE key = ?', (MODE_PICKLE, 'key'))
    with pytest.raises(ValueError, match='pickled value is not read'):
        cache.get('key')
    assert not os.path.exists(marker)


def test_a_pickled_key_is_never_loaded(cache, tmp_path):
    """A pickled key in the database fails as JSON and runs nothing."""
    marker = str(tmp_path / 'marker')
    cache.set(('a', 1), 'value')
    cache._sql('UPDATE Cache SET key = ? WHERE raw = 0', (sqlite3.Binary(pickle.dumps(_Marker(marker))),))
    with pytest.raises(ValueError):
        list(cache)
    assert not os.path.exists(marker)


def test_directories_are_only_for_the_owner(cache):
    """The cache directory and the folders of value files deny group and others."""
    cache.set('key', {'big': 'x' * 100})
    ((filename,),) = cache._sql('SELECT filename FROM Cache WHERE key = ?', ('key',)).fetchall()
    folder = os.path.dirname(os.path.join(cache.directory, filename))
    for path in (cache.directory, folder):
        assert stat.S_IMODE(os.stat(path).st_mode) & 0o077 == 0


def test_lock_holds_the_key_while_locked(cache):
    """Lock keeps its key in the cache until it is released."""
    lock = Lock(cache, 'report')
    with lock:
        assert lock.locked() is True
    assert lock.locked() is False
