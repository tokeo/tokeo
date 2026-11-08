"""
Tests for tokeo.ext.diskcache: the connections of the threads, the locks
temper (concurrency limit) and throttle (rate limit), alone, across threads
and across processes.
"""

import re
import time
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from tokeo.ext.diskcache import TokeoDiskCacheError, _ThreadConnection
from tests.ext.diskcache.diskcache_tests_helper import (
    DiskcacheTest,
    Connections,
    run_in_threads,
    get_max_overlap,
    get_spawn_context,
    collect_results,
    stop_processes,
)

# ---- multiprocessing workers ------------------------------------------------
# the workers must stay importable at module level for spawn


def _temper_worker(directory, count, hold, result_queue):
    # each process opens its own app pointing at the same cache directory;
    # report a payload either way so the parent never waits forever
    try:
        with DiskcacheTest(directory) as app:

            @app.cache.locks.temper(max_calls=count, name='mp_temper', verbose=False)
            def job():
                start = time.time()
                time.sleep(hold)
                return (start, time.time())

            result_queue.put(('ok', job()))
    except BaseException as err:  # noqa: B036
        result_queue.put(('error', repr(err)))


def _throttle_worker(directory, count, per_seconds, result_queue):
    # each process opens its own app pointing at the same cache directory;
    # report a payload either way so the parent never waits forever
    try:
        with DiskcacheTest(directory) as app:

            @app.cache.locks.throttle(
                count=count,
                per_seconds=per_seconds,
                name='mp_throttle',
                cb_on_locked=lambda **kw: 'locked',
                verbose=False,
            )
            def job():
                return 'ran'

            result_queue.put(('ok', job()))
    except BaseException as err:  # noqa: B036
        result_queue.put(('error', repr(err)))


# ---- connections: each thread closes its own when it ends -------------------


@pytest.mark.parametrize('directory', [None, ''], ids=['none', 'empty'])
def test_setup_without_directory_raises(directory):
    """Without directory the setup stops, temp dirs would split the locks."""
    with pytest.raises(TokeoDiskCacheError, match='diskcache.directory must be set'):
        with DiskcacheTest(directory):
            pass


def test_connections_of_threads_close_when_they_end(tmp, monkeypatch):
    """The connection of each thread closes when the thread ends."""
    connections = Connections(monkeypatch)
    with DiskcacheTest(tmp.dir) as app:
        app.cache.set('key', 'value')
        thread = threading.Thread(target=app.cache.get, args=('key',))
        thread.start()
        thread.join()
        assert run_in_threads(lambda: app.cache.get('key')) == ['value'] * 4
        # only the connection of the app is still open
        assert len(connections.unclosed_connections) == 1
    assert connections.unclosed_connections == []


def test_connections_of_threads_close_after_temper_and_throttle(tmp, monkeypatch):
    """Threads running temper and throttle leave no connection open."""
    connections = Connections(monkeypatch)
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.temper(max_calls=4, name='threads', verbose=False)
        def tempered():
            return 'ran'

        @app.cache.locks.throttle(count=4, per_seconds=3600, name='threads', verbose=False)
        def throttled():
            return 'ran'

        assert run_in_threads(tempered) == ['ran'] * 4
        assert run_in_threads(throttled) == ['ran'] * 4
        # the app itself never accessed the cache after its setup
        assert len(connections.opened_connections) > 1
        assert connections.unclosed_connections == []


def test_connections_close_by_hand_and_reopen(tmp, monkeypatch):
    """close() closes the connection, the next access opens a new one."""
    connections = Connections(monkeypatch)
    with DiskcacheTest(tmp.dir) as app:
        app.cache.set('key', 'value')
        app.cache.close()
        assert connections.unclosed_connections == []
        # a later access opens a new connection
        assert app.cache.get('key') == 'value'
        assert len(connections.unclosed_connections) == 1
        # also by hand inside a thread as diskcache describes it
        assert run_in_threads(lambda: (app.cache.get('key'), app.cache.close())[0]) == ['value'] * 4
        assert len(connections.unclosed_connections) == 1
    assert connections.unclosed_connections == []


def test_connections_of_threads_close_after_transactions(tmp, monkeypatch):
    """Threads with transactions leave no connection open."""
    connections = Connections(monkeypatch)
    with DiskcacheTest(tmp.dir) as app:

        def transact():
            with app.cache._cache.transact():
                app.cache.set('key', 'value')
                return app.cache.get('key')

        assert run_in_threads(transact) == ['value'] * 4
        assert app.cache.get('key') == 'value'
        assert len(connections.unclosed_connections) == 1
    assert connections.unclosed_connections == []


def test_connections_stay_open_when_released_in_another_thread():
    """A holder released in another thread leaves the connection open."""
    con = sqlite3.connect(':memory:', check_same_thread=False)
    holder = run_in_threads(lambda: _ThreadConnection(con), workers=1)[0]
    del holder
    assert con.execute('SELECT 1').fetchone() == (1,)
    con.close()


# ---- locks ------------------------------------------------------------------


def test_locks_acquire_returns_true(tmp):
    """acquire returns True and the lock is held until release."""
    with DiskcacheTest(tmp.dir) as app:
        assert app.cache.locks.acquire('acquire') is True
        assert app.cache.locks.locked('acquire') is True
        app.cache.locks.release('acquire')
        assert app.cache.locks.locked('acquire') is False


def _collect_warnings(app, monkeypatch):
    # collect the warnings the app logs
    logged_lines = []
    monkeypatch.setattr(app.log, 'warning', lambda msg, **kw: logged_lines.append(msg))
    return logged_lines


def test_locks_lock_releases_its_own_lock(tmp, monkeypatch):
    """lock() holds the lock in the block and frees it after, no warning."""
    with DiskcacheTest(tmp.dir) as app:
        logged_lines = _collect_warnings(app, monkeypatch)
        with app.cache.locks.lock('own'):
            assert app.cache.locks.locked('own') is True
        assert app.cache.locks.locked('own') is False
        assert logged_lines == []


def test_locks_lock_lost_by_purge_warns(tmp, monkeypatch):
    """A lock purged in the block warns "... lost before release (missing)"."""
    with DiskcacheTest(tmp.dir) as app:
        logged_lines = _collect_warnings(app, monkeypatch)
        with app.cache.locks.lock('lost'):
            app.cache.locks.purge()
        assert logged_lines == ['lock dc_lost was lost before release (missing)']


def test_locks_lock_keeps_the_lock_of_another(tmp, monkeypatch):
    """A lock taken by another after a purge stays held, with a warning."""
    with DiskcacheTest(tmp.dir) as app:
        logged_lines = _collect_warnings(app, monkeypatch)
        with app.cache.locks.lock('other'):
            app.cache.locks.purge()
            # another holder takes the lock after the purge
            app.cache.locks.acquire('other')
        assert app.cache.locks.locked('other') is True
        assert logged_lines == ['lock dc_other was lost before release (taken by another)']


def test_locks_lock_keeps_the_lock_of_another_after_expire(tmp, monkeypatch):
    """A lock taken by another after expire stays held, with a warning."""
    with DiskcacheTest(tmp.dir) as app:
        logged_lines = _collect_warnings(app, monkeypatch)
        with app.cache.locks.lock('expire', expire=0.05):
            time.sleep(0.1)
            app.cache.locks.acquire('expire')
        assert app.cache.locks.locked('expire') is True
        assert logged_lines == ['lock dc_expire was lost before release (taken by another)']


def test_locks_lock_failing_acquire_keeps_the_lock_of_another(tmp, monkeypatch):
    """An interrupted acquire leaves the lock of another held."""
    with DiskcacheTest(tmp.dir) as app:
        app.cache.locks.acquire('busy')

        def interrupt(seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr('tokeo.ext.diskcache.time.sleep', interrupt)
        with pytest.raises(KeyboardInterrupt):
            with app.cache.locks.lock('busy'):
                pass
        assert app.cache.locks.locked('busy') is True


def _collect_sleeps_of_a_waiting_lock(app, monkeypatch, **kw):
    # the seconds a lock waits per check while another holds it
    slept = []
    app.cache.locks.acquire('busy')

    def sleep(seconds):
        slept.append(seconds)
        app.cache.locks.release('busy')

    monkeypatch.setattr('tokeo.ext.diskcache.time.sleep', sleep)
    with app.cache.locks.lock('busy', **kw):
        pass
    return slept


def test_locks_lock_waits_by_locks_sleep_seconds(tmp, monkeypatch):
    """A waiting lock sleeps 0.1 seconds, unless the config or call set it."""
    with DiskcacheTest(tmp.dir) as app:
        assert _collect_sleeps_of_a_waiting_lock(app, monkeypatch) == [0.1]
        assert _collect_sleeps_of_a_waiting_lock(app, monkeypatch, sleep_seconds=0.01) == [0.01]
    with DiskcacheTest(tmp.dir, diskcache_config={'locks_sleep_seconds': 0.2}) as app:
        assert _collect_sleeps_of_a_waiting_lock(app, monkeypatch) == [0.2]


def test_temper_waits_by_locks_sleep_seconds(tmp):
    """A blocked temper call sleeps locks_sleep_seconds of the config by default."""
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        raise RuntimeError('stop waiting')

    with DiskcacheTest(tmp.dir, diskcache_config={'locks_sleep_seconds': 0.2}) as app:

        @app.cache.locks.temper(max_calls=1, name='busy', sleep_func=sleep, verbose=False)
        def inner():
            pass

        @app.cache.locks.temper(max_calls=1, name='busy', verbose=False)
        def outer():
            # the slot is taken by outer, inner has to wait
            with pytest.raises(RuntimeError):
                inner()

        outer()
    assert slept == [0.2]


@pytest.mark.parametrize('sleep_seconds', [0, -1, True, '0.05'], ids=['zero', 'negative', 'bool', 'str'])
def test_locks_sleep_seconds_must_be_a_number_greater_than_0(tmp, sleep_seconds):
    """A locks_sleep_seconds not greater than 0 stops the setup of the cache."""
    expected = f'diskcache.locks_sleep_seconds must be a number of seconds greater than 0, not {sleep_seconds!r}'
    with pytest.raises(TokeoDiskCacheError, match=f'^{re.escape(expected)}$'):
        with DiskcacheTest(tmp.dir, diskcache_config={'locks_sleep_seconds': sleep_seconds}):
            pass


def test_create_locks_handler_refuses_a_sleep_seconds_argument_not_greater_than_0(tmp):
    """sleep_seconds=0 as argument raises a ValueError, an error in the code."""
    with DiskcacheTest(tmp.dir) as app:
        with pytest.raises(ValueError, match='^sleep_seconds must be a number of seconds greater than 0, not 0$') as err_info:
            app.cache.create_locks_handler('tag', 'prefix', sleep_seconds=0)
    assert type(err_info.value) is ValueError


# ---- temper: five essential points ------------------------------------------


def test_temper_acquire_and_release(tmp):
    """A call runs, returns its result and gives its slot back."""
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.temper(max_calls=1, name='single', verbose=False)
        def job():
            return 'ran'

        assert job() == 'ran'
        # a second call must run too, proving the slot was restored
        assert job() == 'ran'
        # the last release removes the key
        assert app.cache.get('dc_single') is None


def test_temper_callback_when_no_slot(tmp):
    """Without a free slot the callback runs instead of the function."""
    with DiskcacheTest(tmp.dir) as app:
        runs = []
        entered_event = threading.Event()
        release = threading.Event()

        @app.cache.locks.temper(
            max_calls=1,
            name='mutex',
            cb_on_locked=lambda **kw: 'locked',
            verbose=False,
        )
        def job():
            runs.append(1)
            entered_event.set()
            release.wait(3.0)
            return 'ran'

        holder = threading.Thread(target=job)
        holder.start()
        # wait until the only slot is held by the background thread
        assert entered_event.wait(3.0)
        # the second call cannot acquire and gets the callback result
        assert job() == 'locked'
        release.set()
        holder.join(3.0)
        # only the holder executed the wrapped body
        assert runs == [1]


def test_temper_releases_on_exception(tmp):
    """An exception in the function still gives the slot back."""
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.temper(max_calls=1, name='boom', verbose=False)
        def boom():
            raise ValueError('intended failure')

        with pytest.raises(ValueError):
            boom()

        # the slot must be released despite the exception
        assert app.cache.get('dc_boom') is None

        @app.cache.locks.temper(max_calls=1, name='boom', verbose=False)
        def job():
            return 'ran'

        # a fresh call acquires without being blocked by a leaked slot
        assert job() == 'ran'


def test_temper_thread_max_concurrency(tmp):
    """Of 8 threads with max_calls 2 never more than 2 run at once."""
    count = 2
    workers = 8
    with DiskcacheTest(tmp.dir) as app:
        intervals = []
        guard = threading.Lock()

        @app.cache.locks.temper(
            max_calls=count,
            name='threads',
            sleep_seconds=0.01,
            verbose=False,
        )
        def job():
            start = time.time()
            time.sleep(0.2)
            end = time.time()
            with guard:
                intervals.append((start, end))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda _: job(), range(workers)))

        # all calls eventually ran and the concurrency never exceeded count
        assert len(intervals) == workers
        assert get_max_overlap(intervals) <= count


def test_temper_process_max_concurrency(tmp):
    """Of 6 processes with max_calls 2 never more than 2 run at once."""
    count = 2
    workers = 6
    ctx = get_spawn_context()
    result_queue = ctx.Queue()
    # fmt: off
    procs = [
        ctx.Process(target=_temper_worker, args=(tmp.dir, count, 0.4, result_queue))
        for _ in range(workers)
    ]
    # fmt: on
    for proc in procs:
        proc.start()
    results = collect_results(result_queue, workers, total_timeout=60)
    stop_processes(procs)

    # every worker reported and none failed during setup or execution
    assert len(results) == workers
    errors = [payload for kind, payload in results if kind == 'error']
    assert errors == []
    intervals = [payload for kind, payload in results if kind == 'ok']
    assert get_max_overlap(intervals) <= count


# ---- throttle: five essential points ----------------------------------------


def test_throttle_allows_burst_up_to_count(tmp):
    """With count 3 the first 3 of 5 calls run, 2 get the callback."""
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.throttle(
            count=3,
            per_seconds=100,
            name='burst',
            cb_on_locked=lambda **kw: 'locked',
            verbose=False,
        )
        def job():
            return 'ran'

        results = [job() for _ in range(5)]
        assert results.count('ran') == 3
        assert results.count('locked') == 2


def test_throttle_refills_over_time(tmp):
    """With 1 per 0.5 s a token is back after 0.7 s."""
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.throttle(
            count=1,
            per_seconds=0.5,
            name='refill',
            cb_on_locked=lambda **kw: 'locked',
            verbose=False,
        )
        def job():
            return 'ran'

        assert job() == 'ran'
        # immediately afterwards the bucket is empty
        assert job() == 'locked'
        # wait long enough for one token to refill
        time.sleep(0.7)
        assert job() == 'ran'


def test_throttle_blocking_delays(tmp):
    """Without callback 4 calls at 1 per second take at least 1.8 s."""
    with DiskcacheTest(tmp.dir) as app:

        # count=2, per_seconds=2 -> rate 1/s; 4 calls = 2 burst + 2 waits ~2s
        @app.cache.locks.throttle(count=2, per_seconds=2, name='blocking', verbose=False)
        def job():
            return 'ran'

        start = time.time()
        for _ in range(4):
            assert job() == 'ran'
        elapsed = time.time() - start
        assert elapsed >= 1.8


def test_throttle_thread_max_rate(tmp):
    """Of 10 threads with count 3 exactly 3 run, 7 get the callback."""
    count = 3
    workers = 10
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.throttle(
            count=count,
            per_seconds=100,
            name='threads',
            cb_on_locked=lambda **kw: 'locked',
            verbose=False,
        )
        def job():
            return 'ran'

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda _: job(), range(workers)))

        assert results.count('ran') == count
        assert results.count('locked') == workers - count


def test_throttle_process_max_rate(tmp):
    """Of 10 processes with count 3 exactly 3 run, 7 get the callback."""
    count = 3
    workers = 10
    # a large window keeps refill negligible during the (spawn) startup spread
    per_seconds = 3600
    ctx = get_spawn_context()
    result_queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=_throttle_worker,
            args=(tmp.dir, count, per_seconds, result_queue),
        )
        for _ in range(workers)
    ]
    for proc in procs:
        proc.start()
    results = collect_results(result_queue, workers, total_timeout=60)
    stop_processes(procs)

    # every worker reported and none failed during setup or execution
    assert len(results) == workers
    errors = [payload for kind, payload in results if kind == 'error']
    assert errors == []
    payloads = [payload for kind, payload in results if kind == 'ok']
    assert payloads.count('ran') == count
    assert payloads.count('locked') == workers - count


# ---- expire: a default for every key, only numbers greater than 0 -----------


def _get_expire_seconds(app, key):
    # the seconds until the key expires
    _, expire_time = app.cache._cache.get(key, expire_time=True)
    return expire_time - time.time()


@pytest.mark.parametrize('expire', [None, 0, -1, True, '60'], ids=['none', 'zero', 'negative', 'bool', 'str'])
def test_expire_must_be_a_number_greater_than_0(tmp, expire):
    """temper, throttle, lock and acquire refuse an expire not greater than 0."""
    with DiskcacheTest(tmp.dir) as app:
        with pytest.raises(ValueError, match='expire must be a number of seconds greater than 0'):
            app.cache.locks.temper(expire=expire)
        if expire is not None:
            with pytest.raises(ValueError, match='expire must be a number of seconds greater than 0'):
                app.cache.locks.throttle(expire=expire)
        with pytest.raises(ValueError, match='expire must be a number of seconds greater than 0'):
            with app.cache.locks.lock('bad', expire=expire):
                pass
        with pytest.raises(ValueError, match='expire must be a number of seconds greater than 0'):
            app.cache.locks.acquire('bad', expire=expire)
        assert app.cache.locks.locked('bad') is False


def test_throttle_expire_must_not_be_below_per_seconds(tmp):
    """throttle refuses an expire below per_seconds and takes one equal to it."""
    with DiskcacheTest(tmp.dir) as app:
        with pytest.raises(ValueError, match='expire must not be below per_seconds 60'):
            app.cache.locks.throttle(count=10, per_seconds=60, expire=5)
        assert app.cache.locks.throttle(count=10, per_seconds=60, expire=60)


@pytest.mark.parametrize('count', [0, -1, True, 1.5, '2'], ids=['zero', 'negative', 'bool', 'float', 'str'])
def test_throttle_count_must_be_an_integer_greater_than_0(tmp, count):
    """throttle refuses a count that is no integer greater than 0."""
    with DiskcacheTest(tmp.dir) as app:
        with pytest.raises(ValueError, match='count must be an integer greater than 0'):
            app.cache.locks.throttle(count=count)


@pytest.mark.parametrize('per_seconds', [0, -1, True, '60', float('inf')], ids=['zero', 'negative', 'bool', 'str', 'inf'])
def test_throttle_per_seconds_must_be_a_number_greater_than_0(tmp, per_seconds):
    """throttle refuses a per_seconds that is no number greater than 0."""
    with DiskcacheTest(tmp.dir) as app:
        with pytest.raises(ValueError, match='per_seconds must be a number of seconds greater than 0'):
            app.cache.locks.throttle(per_seconds=per_seconds)


def test_throttle_expire_defaults_to_per_seconds_plus_10(tmp):
    """Without expire the throttle key expires 10 s after per_seconds."""
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.throttle(count=1, per_seconds=60, name='default', verbose=False)
        def job():
            return 'ran'

        assert job() == 'ran'
        assert 69 < _get_expire_seconds(app, 'dc_default') <= 70


def test_temper_lock_and_acquire_expire_defaults_to_3600(tmp):
    """Without expire the keys of temper, lock and acquire expire after 3600 s."""
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.temper(name='default', verbose=False)
        def job():
            return _get_expire_seconds(app, 'dc_default')

        assert 3599 < job() <= 3600
        with app.cache.locks.lock('lock'):
            assert 3599 < _get_expire_seconds(app, 'dc_lock') <= 3600
        app.cache.locks.acquire('acquire')
        assert 3599 < _get_expire_seconds(app, 'dc_acquire') <= 3600


def test_throttle_and_temper_log_nothing_by_default(tmp, monkeypatch):
    """Without verbose throttle and temper write no info line per call."""
    with DiskcacheTest(tmp.dir) as app:
        infos = []
        monkeypatch.setattr(app.log, 'info', lambda msg, *args, **kwargs: infos.append(msg))

        @app.cache.locks.throttle(count=1, per_seconds=60, name='quiet_throttle')
        @app.cache.locks.temper(name='quiet_temper')
        def job():
            return 'ran'

        assert job() == 'ran'
        assert infos == []


# ---- temper: the expire times of the holders --------------------------------


def test_temper_holds_its_slot_by_an_expire_time(tmp):
    """A call holds one expire time of now plus expire, the key lives as long."""
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.temper(max_calls=2, name='expire_time', expire=60, verbose=False)
        def job():
            return app.cache.get('dc_expire_time'), _get_expire_seconds(app, 'dc_expire_time')

        start_time = time.time()
        expire_times, expire_seconds = job()
        assert len(expire_times) == 1
        assert start_time + 60 <= expire_times[0] <= time.time() + 60
        assert 59 < expire_seconds <= 60


def test_temper_drops_an_expired_holder_while_others_run(tmp, monkeypatch):
    """The expire time of a killed holder frees its slot, a running one stays."""
    with DiskcacheTest(tmp.dir) as app:
        logged_lines = _collect_warnings(app, monkeypatch)
        now = time.time()
        # count 2 is full: a killed holder and one still running
        app.cache.set('dc_dead', [now - 1, now + 100], expire=100)

        @app.cache.locks.temper(max_calls=2, name='dead', cb_on_locked=lambda **kw: 'locked', verbose=False)
        def job():
            return len(app.cache.get('dc_dead'))

        assert job() == 2
        assert app.cache.get('dc_dead') == [now + 100]
        assert logged_lines == ['@temper dc_dead dropped 1 expired slot(s)']


def test_temper_key_lives_until_the_latest_expire_time(tmp):
    """A call with a short expire keeps the longer expire time of another."""
    with DiskcacheTest(tmp.dir) as app:
        now = time.time()
        # another call with expire 3600 holds a slot under the same name
        app.cache.set('dc_shared', [now + 3600], expire=3600)

        @app.cache.locks.temper(max_calls=2, name='shared', expire=60, verbose=False)
        def job():
            return _get_expire_seconds(app, 'dc_shared')

        assert 3599 < job() <= 3600
        assert app.cache.get('dc_shared') == [now + 3600]
        assert 3599 < _get_expire_seconds(app, 'dc_shared') <= 3600


@pytest.mark.parametrize('loss', ['purge', 'foreign'])
def test_temper_warns_when_its_slot_was_lost(tmp, monkeypatch, loss):
    """A call whose expire time is gone on release warns and recreates no key."""
    with DiskcacheTest(tmp.dir) as app:
        logged_lines = _collect_warnings(app, monkeypatch)

        @app.cache.locks.temper(name='lost', verbose=False)
        def job():
            if loss == 'purge':
                app.cache.locks.purge()
            else:
                app.cache.set('dc_lost', ['foreign'])
            return 'ran'

        assert job() == 'ran'
        assert app.cache.get('dc_lost') == (None if loss == 'purge' else ['foreign'])
        assert logged_lines == ['@temper dc_lost slot was lost before release']


@pytest.mark.parametrize('value', [1, ['x', 'y']], ids=['counter', 'foreign'])
def test_temper_takes_another_value_as_no_holder(tmp, value):
    """A counter of an older version or a foreign list holds no slot."""
    with DiskcacheTest(tmp.dir) as app:
        app.cache.set('dc_other', value)

        @app.cache.locks.temper(name='other', cb_on_locked=lambda **kw: 'locked', verbose=False)
        def job():
            return 'ran'

        assert job() == 'ran'
        assert app.cache.get('dc_other') is None


@pytest.mark.parametrize('sleep_seconds', [0, -1, True, '0.1', float('inf')], ids=['zero', 'negative', 'bool', 'str', 'inf'])
def test_temper_sleep_seconds_must_be_a_number_greater_than_0(tmp, sleep_seconds):
    """temper refuses an own sleep_seconds that is no number greater than 0."""
    with DiskcacheTest(tmp.dir) as app:
        with pytest.raises(ValueError, match='sleep_seconds must be a number of seconds greater than 0'):
            app.cache.locks.temper(sleep_seconds=sleep_seconds)


@pytest.mark.parametrize('max_calls', [0, -1, True, 1.5, '2'], ids=['zero', 'negative', 'bool', 'float', 'str'])
def test_temper_max_calls_must_be_an_integer_greater_than_0(tmp, max_calls):
    """temper refuses a max_calls that is no integer greater than 0."""
    with DiskcacheTest(tmp.dir) as app:
        with pytest.raises(ValueError, match='max_calls must be an integer greater than 0'):
            app.cache.locks.temper(max_calls=max_calls)


@pytest.mark.parametrize('text, value', [('False', False), ('no', False), ('0', False), ('True', True), ('on', True)])
def test_cache_set_reads_a_bool_by_its_words(tmp, text, value):
    """cache set --value-type bool stores False for 'False', 'no' and '0'."""
    with DiskcacheTest(tmp.dir, argv=['cache', 'set', 'flag', '--value', text, '--value-type', 'bool']) as app:
        app.run()
        assert app.cache.get('flag') is value


def test_cache_set_refuses_a_bool_it_cannot_read(tmp):
    """cache set --value-type bool with any other word stores nothing, exit 1."""
    with DiskcacheTest(tmp.dir, argv=['cache', 'set', 'flag', '--value', 'maybe', '--value-type', 'bool']) as app:
        app.run()
        assert app.cache.get('flag') is None
        assert app.exit_code == 1


def test_cache_list_shows_the_whole_seconds_until_a_key_expires(tmp, capsys):
    """cache list --with-expires shows the rest in whole seconds, or no expire."""
    with DiskcacheTest(tmp.dir, argv=['cache', 'list', '--with-expires']) as app:
        app.cache.set('short', 1, expire=60)
        app.cache.set('kept', 1)
        app.run()
    lines = capsys.readouterr().out.splitlines()
    assert 'short (60s)' in lines or 'short (59s)' in lines
    assert 'kept (no expire)' in lines


def test_cache_locks_lists_a_held_lock_with_its_expire(tmp, capsys):
    """cache locks shows the lock tag and a held lock with its rest in seconds."""
    with DiskcacheTest(tmp.dir, argv=['cache', 'locks']) as app:
        assert app.cache.locks.acquire('job', expire=60)
        app.run()
    out = capsys.readouterr().out
    assert f'Locks using the tag: {app.cache.locks._tag}' in out
    lock_line = f'{app.cache.locks._key_prefix}job (60s) [{app.cache.locks._tag}]'
    assert lock_line in out.splitlines() or lock_line.replace('60s', '59s') in out.splitlines()


def test_cache_list_shows_a_value_of_none_with_its_type(tmp, capsys):
    """cache list --with-values --with-types shows None, not an empty str."""
    with DiskcacheTest(tmp.dir, argv=['cache', 'list', '--with-values', '--with-types']) as app:
        app.cache.set('nothing', None)
        app.run()
    assert 'nothing = None |:NoneType|' in capsys.readouterr().out.splitlines()
