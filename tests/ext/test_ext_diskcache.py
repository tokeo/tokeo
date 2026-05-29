import time
import threading
import multiprocessing as mp
from queue import Empty
from concurrent.futures import ThreadPoolExecutor

import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def max_overlap(intervals):
    # compute the maximum number of overlapping (start, end) intervals;
    # an end at the same instant as a start does not count as overlap
    events = []
    for start, end in intervals:
        events.append((start, 1))
        events.append((end, -1))
    # at equal timestamps process the end (-1) before the start (+1)
    events.sort(key=lambda e: (e[0], e[1]))
    current = peak = 0
    for _, delta in events:
        current += delta
        if current > peak:
            peak = current
    return peak


class DiskcacheTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.diskcache',
        ]


def cache_defaults(directory):
    # pin the diskcache to a fixed directory so that threads and separate
    # processes coordinate through the very same cache database
    d = init_defaults('diskcache')
    d['diskcache']['directory'] = directory
    return d


def spawn_context():
    # always use spawn for the multiprocessing tests; fork would inherit the
    # parent's open sqlite/diskcache handles and segfault on some platforms
    # (e.g. macos). spawn starts a clean interpreter and is stable everywhere.
    return mp.get_context('spawn')


def collect_results(result_queue, expected, total_timeout):
    # gather exactly expected payloads, but never block forever if a child
    # dies without reporting; returns whatever arrived before the deadline
    items = []
    deadline = time.monotonic() + total_timeout
    for _ in range(expected):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            items.append(result_queue.get(timeout=remaining))
        except Empty:
            break
    return items


def stop_processes(procs):
    # join with a small grace period and terminate stragglers to keep the
    # test suite from hanging on a misbehaving child
    for proc in procs:
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()


# --------------------------------------------------------------------------------------
# multiprocessing workers (must stay importable at module level for spawn)
# --------------------------------------------------------------------------------------


def temper_worker(directory, count, hold, result_queue):
    # each process opens its own app pointing at the same cache directory;
    # report a payload either way so the parent never waits forever
    try:
        with DiskcacheTest(config_defaults=cache_defaults(directory)) as app:

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
        with DiskcacheTest(config_defaults=cache_defaults(directory)) as app:

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
# temper: five essential points
# --------------------------------------------------------------------------------------


def test_temper_acquire_and_release(tmp):
    # 1) a single call runs, returns its result and restores the slot
    with DiskcacheTest(config_defaults=cache_defaults(tmp.dir)) as app:

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
    with DiskcacheTest(config_defaults=cache_defaults(tmp.dir)) as app:
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
    with DiskcacheTest(config_defaults=cache_defaults(tmp.dir)) as app:

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
    with DiskcacheTest(config_defaults=cache_defaults(tmp.dir)) as app:
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
    with DiskcacheTest(config_defaults=cache_defaults(tmp.dir)) as app:

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
    with DiskcacheTest(config_defaults=cache_defaults(tmp.dir)) as app:

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
    with DiskcacheTest(config_defaults=cache_defaults(tmp.dir)) as app:

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
    with DiskcacheTest(config_defaults=cache_defaults(tmp.dir)) as app:

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
