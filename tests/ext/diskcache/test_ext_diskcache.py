"""
Tests for tokeo.ext.diskcache: the connections of the threads, the locks
temper (concurrency limit) and throttle (rate limit), alone, across threads
and across processes.
"""

import time
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from tokeo.ext.diskcache import _ThreadConnection
from tests.ext.diskcache.diskcache_tests_helper import (
    DiskcacheTest,
    Connections,
    in_threads,
    max_overlap,
    spawn_context,
    collect_results,
    stop_processes,
)

# --------------------------------------------------------------------------------------
# multiprocessing workers (must stay importable at module level for spawn)
# --------------------------------------------------------------------------------------


def temper_worker(directory, count, hold, result_queue):
    # each process opens its own app pointing at the same cache directory;
    # report a payload either way so the parent never waits forever
    try:
        with DiskcacheTest(directory) as app:

            @app.cache.locks.temper(count=count, name='mp_temper', verbose=False)
            def job():
                start = time.time()
                time.sleep(hold)
                return (start, time.time())

            result_queue.put(('ok', job()))
    except BaseException as err:  # noqa: B036
        result_queue.put(('error', repr(err)))


def throttle_worker(directory, count, per_seconds, result_queue):
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


# --------------------------------------------------------------------------------------
# connections: each thread closes its own when it ends
# --------------------------------------------------------------------------------------


def test_connections_of_threads_close_when_they_end(tmp, monkeypatch):
    connections = Connections(monkeypatch)
    with DiskcacheTest(tmp.dir) as app:
        app.cache.set('key', 'value')
        thread = threading.Thread(target=app.cache.get, args=('key',))
        thread.start()
        thread.join()
        assert in_threads(lambda: app.cache.get('key')) == ['value'] * 4
        # only the connection of the app is still open
        assert len(connections.unclosed) == 1
    assert connections.unclosed == []


def test_connections_of_threads_close_after_temper_and_throttle(tmp, monkeypatch):
    connections = Connections(monkeypatch)
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.temper(count=4, name='threads', verbose=False)
        def tempered():
            return 'ran'

        @app.cache.locks.throttle(count=4, per_seconds=3600, name='threads', verbose=False)
        def throttled():
            return 'ran'

        assert in_threads(tempered) == ['ran'] * 4
        assert in_threads(throttled) == ['ran'] * 4
        # the app itself never accessed the cache after its setup
        assert len(connections.opened) > 1
        assert connections.unclosed == []


def test_connections_close_by_hand_and_reopen(tmp, monkeypatch):
    connections = Connections(monkeypatch)
    with DiskcacheTest(tmp.dir) as app:
        app.cache.set('key', 'value')
        app.cache.close()
        assert connections.unclosed == []
        # a later access opens a new connection
        assert app.cache.get('key') == 'value'
        assert len(connections.unclosed) == 1
        # also by hand inside a thread as diskcache describes it
        assert in_threads(lambda: (app.cache.get('key'), app.cache.close())[0]) == ['value'] * 4
        assert len(connections.unclosed) == 1
    assert connections.unclosed == []


def test_connections_of_threads_close_after_transactions(tmp, monkeypatch):
    connections = Connections(monkeypatch)
    with DiskcacheTest(tmp.dir) as app:

        def transact():
            with app.cache._cache.transact():
                app.cache.set('key', 'value')
                return app.cache.get('key')

        assert in_threads(transact) == ['value'] * 4
        assert app.cache.get('key') == 'value'
        assert len(connections.unclosed) == 1
    assert connections.unclosed == []


def test_connections_stay_open_when_released_in_another_thread():
    # sqlite closes only in its own thread, so the release leaves it to sqlite
    con = sqlite3.connect(':memory:', check_same_thread=False)
    holder = in_threads(lambda: _ThreadConnection(con), workers=1)[0]
    del holder
    assert con.execute('SELECT 1').fetchone() == (1,)
    con.close()


# --------------------------------------------------------------------------------------
# locks
# --------------------------------------------------------------------------------------


def test_locks_acquire_returns_true(tmp):
    with DiskcacheTest(tmp.dir) as app:
        assert app.cache.locks.acquire('acquire') is True
        assert app.cache.locks.locked('acquire') is True
        app.cache.locks.release('acquire')
        assert app.cache.locks.locked('acquire') is False


def warnings_of(app, monkeypatch):
    # collect the warnings the app logs
    logged = []
    monkeypatch.setattr(app.log, 'warning', lambda msg, **kw: logged.append(msg))
    return logged


def test_locks_lock_releases_its_own_lock(tmp, monkeypatch):
    with DiskcacheTest(tmp.dir) as app:
        logged = warnings_of(app, monkeypatch)
        with app.cache.locks.lock('own'):
            assert app.cache.locks.locked('own') is True
        assert app.cache.locks.locked('own') is False
        assert logged == []


def test_locks_lock_lost_by_purge_warns(tmp, monkeypatch):
    with DiskcacheTest(tmp.dir) as app:
        logged = warnings_of(app, monkeypatch)
        with app.cache.locks.lock('lost'):
            app.cache.locks.purge()
        assert logged == ['lock dc_lost was lost before release (missing)']


def test_locks_lock_keeps_the_lock_of_another(tmp, monkeypatch):
    with DiskcacheTest(tmp.dir) as app:
        logged = warnings_of(app, monkeypatch)
        with app.cache.locks.lock('other'):
            app.cache.locks.purge()
            # another holder takes the lock after the purge
            app.cache.locks.acquire('other')
        assert app.cache.locks.locked('other') is True
        assert logged == ['lock dc_other was lost before release (taken by another)']


def test_locks_lock_keeps_the_lock_of_another_after_expire(tmp, monkeypatch):
    with DiskcacheTest(tmp.dir) as app:
        logged = warnings_of(app, monkeypatch)
        with app.cache.locks.lock('expire', expire=0.05):
            time.sleep(0.1)
            app.cache.locks.acquire('expire')
        assert app.cache.locks.locked('expire') is True
        assert logged == ['lock dc_expire was lost before release (taken by another)']


def test_locks_lock_failing_acquire_keeps_the_lock_of_another(tmp, monkeypatch):
    with DiskcacheTest(tmp.dir) as app:
        app.cache.locks.acquire('busy')

        def interrupt(seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr('tokeo.ext.diskcache.time.sleep', interrupt)
        with pytest.raises(KeyboardInterrupt):
            with app.cache.locks.lock('busy'):
                pass
        assert app.cache.locks.locked('busy') is True


# --------------------------------------------------------------------------------------
# temper: five essential points
# --------------------------------------------------------------------------------------


def test_temper_acquire_and_release(tmp):
    # 1) a single call runs, returns its result and restores the slot
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.temper(count=1, name='single', verbose=False)
        def job():
            return 'ran'

        assert job() == 'ran'
        # a second call must run too, proving the slot was restored
        assert job() == 'ran'
        # the slot counter is back to count
        assert app.cache.get('dc_single') == 1


def test_temper_callback_when_no_slot(tmp):
    # 2) when no slot is free the callback fires instead of the function
    with DiskcacheTest(tmp.dir) as app:
        runs = []
        entered = threading.Event()
        release = threading.Event()

        @app.cache.locks.temper(
            count=1,
            name='mutex',
            cb_on_locked=lambda **kw: 'locked',
            verbose=False,
        )
        def job():
            runs.append(1)
            entered.set()
            release.wait(3.0)
            return 'ran'

        holder = threading.Thread(target=job)
        holder.start()
        # wait until the only slot is held by the background thread
        assert entered.wait(3.0)
        # the second call cannot acquire and gets the callback result
        assert job() == 'locked'
        release.set()
        holder.join(3.0)
        # only the holder executed the wrapped body
        assert runs == [1]


def test_temper_releases_on_exception(tmp):
    # 3) an exception inside the body still releases the slot (try/finally)
    with DiskcacheTest(tmp.dir) as app:

        @app.cache.locks.temper(count=1, name='boom', verbose=False)
        def boom():
            raise ValueError('intended failure')

        with pytest.raises(ValueError):
            boom()

        # the slot must be back to count despite the exception
        assert app.cache.get('dc_boom') == 1

        @app.cache.locks.temper(count=1, name='boom', verbose=False)
        def job():
            return 'ran'

        # a fresh call acquires without being blocked by a leaked slot
        assert job() == 'ran'


def test_temper_thread_concurrency_cap(tmp):
    # 4) across many threads, never more than count run at the same time
    count = 2
    workers = 8
    with DiskcacheTest(tmp.dir) as app:
        intervals = []
        guard = threading.Lock()

        @app.cache.locks.temper(
            count=count,
            name='threads',
            sleep_time=0.01,
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
        assert max_overlap(intervals) <= count


def test_temper_process_concurrency_cap(tmp):
    # 5) across separate processes sharing the cache, the cap still holds
    count = 2
    workers = 6
    ctx = spawn_context()
    result_queue = ctx.Queue()
    # fmt: off
    procs = [
        ctx.Process(target=temper_worker, args=(tmp.dir, count, 0.4, result_queue))
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
    assert max_overlap(intervals) <= count


# --------------------------------------------------------------------------------------
# throttle: five essential points
# --------------------------------------------------------------------------------------


def test_throttle_allows_burst_up_to_count(tmp):
    # 1) the first count calls pass, the next ones are rate limited
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
    # 2) after enough time a fresh token becomes available again
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
    # 3) without a callback extra calls block for about (extra / rate)
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


def test_throttle_thread_rate_cap(tmp):
    # 4) many threads at once -> only count run, the rest are limited
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


def test_throttle_process_rate_cap(tmp):
    # 5) across separate processes sharing the cache, only count run
    count = 3
    workers = 10
    # a large window keeps refill negligible during the (spawn) startup spread
    per_seconds = 3600
    ctx = spawn_context()
    result_queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=throttle_worker,
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
