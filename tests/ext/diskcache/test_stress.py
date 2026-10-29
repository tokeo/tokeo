"""
Stress tests of tokeo.ext.diskcache: threads in three processes run random
operations on one cache, once without and once with purges in between.

They run only with STRESSTESTS=1, STRESS_SECONDS sets the duration of each
run, more than 2.5 s, and STRESS_SEED repeats a run.
"""

import os
import sys
import time
import random
import hashlib
import sqlite3
import threading
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.utils import stresstest
from tests.ext.diskcache.diskcache_tests_helper import (
    DiskcacheTest,
    Connections,
    max_overlap,
    spawn_context,
    collect_results,
    stop_processes,
)

pytestmark = stresstest

SECONDS = float(os.environ.get('STRESS_SECONDS', 7.5))
SEED = int(os.environ.get('STRESS_SEED', random.randrange(2**32)))
PROCESSES = 2
# the last seconds of a purge run have no purges, only shown as figures
TAIL = 2.5
ACCOUNTS, BALANCE = 10, 100
TEMPERS = {'stress_t1': 1, 'stress_t3': 3}
# name: (count, per_seconds, blocking)
THROTTLES = {'stress_r': (5, 1, False), 'stress_rs': (2, 0.1, True)}
WEIGHTS = dict(
    get=25,
    set=15,
    delete=5,
    add=5,
    touch=3,
    counter=8,
    transfer=8,
    audit=4,
    temper=8,
    throttle=8,
    mutex=4,
    thread=2,
)


def _payload(rnd):
    # mostly small values, sometimes above 32 KiB so diskcache stores them in files
    size = rnd.choice((0, 10, 1000, 10000, 40000, 100000))
    return rnd.randbytes(size)


def _value_of(key, data):
    return (key, hashlib.sha256(data).hexdigest(), data)


def _wrong_value(key, value):
    # a value is right when it is missing or belongs to its key and is complete
    if value is None:
        return None
    if type(value) is not tuple or value[0] != key or hashlib.sha256(value[2]).hexdigest() != value[1]:
        return f'{key}: {value!r:.80}'
    return None


def _accounts(app):
    # read all accounts in a transaction, set them up again when one is missing
    keys = [f'acc:{n}' for n in range(ACCOUNTS)]
    values = [app.cache.get(key, retry=True) for key in keys]
    if None in values:
        for key in keys:
            app.cache.set(key, BALANCE, retry=True)
        values = [BALANCE] * ACCOUNTS
    return keys, values


class Participant:
    """One process of the stress test with its threads."""

    def __init__(self, app, index, seed, seconds, purge):
        self.app, self.index, self.seed, self.seconds, self.purge = app, index, seed, seconds, purge
        self.lock = threading.Lock()
        self.report = dict(errors=[], wrong=[], lost=[], ops=Counter(), counter=0, tempers={}, mutex=[], throttles={}, purges=[])
        self.report['tempers'] = {name: [] for name in TEMPERS}
        self.report['throttles'] = {name: [] for name in THROTTLES}
        self.tempers = {name: self.tempered(name, count) for name, count in TEMPERS.items()}
        self.throttles = {name: self.throttled(name, *spec) for name, spec in THROTTLES.items()}
        # the warnings of locks that were lost before their release
        self.app.log.warning = lambda msg, **kw: self.record_lost(msg)

    def tempered(self, name, count):
        @self.app.cache.locks.temper(count=count, name=name, sleep_time=0.005, verbose=False)
        def job(hold):
            start = time.time()
            time.sleep(hold)
            return start, time.time()

        @self.app.cache.locks.temper(count=count, name=name, cb_on_locked=lambda **kw: None, verbose=False)
        def job_or_skip(hold):
            return job.__wrapped__(hold)

        return job, job_or_skip

    def throttled(self, name, count, per_seconds, blocking):
        kw = {} if blocking else dict(cb_on_locked=lambda **kw: None)

        @self.app.cache.locks.throttle(count=count, per_seconds=per_seconds, name=name, verbose=False, **kw)
        def job():
            return time.time()

        return job

    def record(self, field, name, item):
        with self.lock:
            self.report[field][name].append(item)

    def record_lost(self, msg):
        with self.lock:
            self.report['lost'].append(msg)

    def wrong(self, text):
        if text:
            with self.lock:
                self.report['wrong'].append(text)

    # the operations, each gets the random generator of its thread

    def op_get(self, rnd):
        key = f'd:{rnd.randrange(200)}'
        self.wrong(_wrong_value(key, self.app.cache.get(key, retry=rnd.random() < 0.5)))

    def op_set(self, rnd):
        key = f'd:{rnd.randrange(200)}'
        self.app.cache.set(key, _value_of(key, _payload(rnd)), tag='stress', retry=rnd.random() < 0.5)

    def op_delete(self, rnd):
        self.app.cache.delete(f'd:{rnd.randrange(200)}', retry=rnd.random() < 0.5)

    def op_add(self, rnd):
        key = f'e:{rnd.randrange(50)}'
        self.app.cache.add(key, _value_of(key, _payload(rnd)), expire=rnd.uniform(0.01, 0.5), retry=rnd.random() < 0.5)
        self.wrong(_wrong_value(key, self.app.cache.get(key, retry=rnd.random() < 0.5)))

    def op_touch(self, rnd):
        self.app.cache.touch(f'e:{rnd.randrange(50)}', expire=rnd.uniform(0.01, 0.5), retry=rnd.random() < 0.5)

    def op_counter(self, rnd):
        with self.app.cache.transact(retry=True):
            value = self.app.cache.get('counter', 0, retry=True)
            self.app.cache.set('counter', value + 1, retry=True)
        with self.lock:
            self.report['counter'] += 1

    def op_transfer(self, rnd):
        with self.app.cache.transact(retry=True):
            keys, values = _accounts(self.app)
            a, b = rnd.sample(range(ACCOUNTS), 2)
            amount = rnd.randint(0, values[a])
            self.app.cache.set(keys[a], values[a] - amount, retry=True)
            self.app.cache.set(keys[b], values[b] + amount, retry=True)

    def op_audit(self, rnd):
        with self.app.cache.transact(retry=True):
            _, values = _accounts(self.app)
        if sum(values) != ACCOUNTS * BALANCE or min(values) < 0:
            self.wrong(f'accounts: {values}')

    def op_temper(self, rnd):
        name = rnd.choice(list(TEMPERS))
        job, job_or_skip = self.tempers[name]
        interval = (job if rnd.random() < 0.5 else job_or_skip)(rnd.uniform(0.001, 0.02))
        if interval:
            self.record('tempers', name, interval)
        # the deadlines in the cache are sorted and never more than the count
        value = self.app.cache.get(f'dc_{name}', retry=True)
        if value is not None and not (type(value) is list and len(value) <= TEMPERS[name] and value == sorted(value)):
            self.wrong(f'{name}: {value}')

    def op_throttle(self, rnd):
        name = rnd.choice(list(THROTTLES))
        ran = self.throttles[name]()
        if ran:
            self.record('throttles', name, ran)

    def op_mutex(self, rnd):
        with self.app.cache.locks.lock('stress_m'):
            start = time.time()
            time.sleep(rnd.uniform(0, 0.003))
            end = time.time()
        with self.lock:
            self.report['mutex'].append((start, end))

    def op_thread(self, rnd):
        # a short thread, its connection has to close when it ends
        seed = rnd.randrange(2**32)
        thread = threading.Thread(target=self.work, args=(seed, time.monotonic() + rnd.uniform(0, 0.05)))
        thread.start()
        thread.join()

    def op_purge(self, rnd):
        choice = rnd.choice(('purge', 'locks', 'evict', 'expire'))
        if choice == 'purge':
            self.app.cache.purge(retry=True)
        elif choice == 'locks':
            self.app.cache.locks.purge()
        elif choice == 'evict':
            self.app.cache.evict('stress', retry=True)
        else:
            self.app.cache.expire(retry=True)
        with self.lock:
            self.report['ops'][f'purge.{choice}'] += 1
            self.report['purges'].append(time.time())

    def work(self, seed, until):
        rnd = random.Random(seed)
        names, weights = list(WEIGHTS), list(WEIGHTS.values())
        while time.monotonic() < until:
            name = rnd.choices(names, weights)[0]
            try:
                getattr(self, f'op_{name}')(rnd)
            except Exception:
                with self.lock:
                    self.report['errors'].append(f'{name}: {traceback.format_exc(limit=-3)}')
            with self.lock:
                self.report['ops'][name] += 1

    def purger(self, seed, until):
        rnd = random.Random(seed)
        while time.monotonic() < until:
            time.sleep(rnd.uniform(0.05, 0.5))
            try:
                self.op_purge(rnd)
            except Exception:
                with self.lock:
                    self.report['errors'].append(f'purge: {traceback.format_exc(limit=-3)}')

    def run(self):
        rnd = random.Random(self.seed)
        unraisable, sys.unraisablehook = sys.unraisablehook, lambda args: self.report['errors'].append(f'unraisable: {args.exc_value!r}')
        try:
            until = time.monotonic() + self.seconds
            workers = rnd.randint(4, 8)
            with ThreadPoolExecutor(max_workers=workers + 1) as pool:
                futures = [pool.submit(self.work, rnd.randrange(2**32), until) for _ in range(workers)]
                if self.purge:
                    futures.append(pool.submit(self.purger, rnd.randrange(2**32), until - TAIL))
                for future in futures:
                    future.result()
        finally:
            sys.unraisablehook = unraisable
        self.report['workers'] = workers
        return self.report


def _stress_worker(directory, index, seed, seconds, purge, result_queue):
    try:
        with DiskcacheTest(directory) as app:
            result_queue.put(('ok', Participant(app, index, seed, seconds, purge).run()))
    except BaseException:  # noqa: B036
        result_queue.put(('error', traceback.format_exc()))


def _merge(reports):
    merged = dict(errors=[], wrong=[], lost=[], ops=Counter(), counter=0, mutex=[], purges=[], workers=0)
    merged['tempers'] = {name: [] for name in TEMPERS}
    merged['throttles'] = {name: [] for name in THROTTLES}
    for report in reports:
        for field in ('errors', 'wrong', 'lost', 'mutex', 'purges'):
            merged[field] += report[field]
        merged['ops'] += report['ops']
        merged['counter'] += report['counter']
        merged['workers'] += report['workers']
        for field in ('tempers', 'throttles'):
            for name, items in report[field].items():
                merged[field][name] += items
    return merged


def _throttle_excess(times, count, per_seconds):
    # the most runs any time window had above what the bucket allows
    times, rate, worst = sorted(times), count / per_seconds, 0
    for i, start in enumerate(times):
        for j in range(i, len(times)):
            # one token of slack for the gap between taking it and the timestamp
            worst = max(worst, (j - i + 1) - (count + rate * (times[j] - start) + 1))
    return worst


def _after(report, since):
    # the runs that started after a point in time
    tail = dict(report, mutex=[item for item in report['mutex'] if item[0] > since])
    tail['tempers'] = {name: [item for item in items if item[0] > since] for name, items in report['tempers'].items()}
    tail['throttles'] = {name: [item for item in items if item > since] for name, items in report['throttles'].items()}
    return tail


def _show(report, title):
    print(f'{title}: mutex runs={len(report["mutex"])} overlap={max_overlap(report["mutex"])}')
    for name, count in TEMPERS.items():
        print(f'{title}: {name} count={count} runs={len(report["tempers"][name])} overlap={max_overlap(report["tempers"][name])}')
    for name, (count, per_seconds, _) in THROTTLES.items():
        times = report['throttles'][name]
        print(f'{title}: {name} runs={len(times)} excess={_throttle_excess(times, count, per_seconds):.2f}')


def _stress(directory, monkeypatch, purge):
    if SECONDS <= TAIL:
        # a purge run keeps its last TAIL seconds free of purges
        pytest.fail(f'STRESS_SECONDS={SECONDS} is too short, it must be above {TAIL}', pytrace=False)
    seed = SEED + purge
    print(f'\nSTRESS_SEED={SEED} purge={purge} seconds={SECONDS}')
    connections = Connections(monkeypatch)
    ctx = spawn_context()
    result_queue = ctx.Queue()
    with DiskcacheTest(directory) as app:
        app.cache.set('counter', 0)
        procs = [
            ctx.Process(target=_stress_worker, args=(directory, index, seed + index, SECONDS, purge, result_queue))
            for index in range(1, PROCESSES + 1)
        ]
        for proc in procs:
            proc.start()
        own = Participant(app, 0, seed, SECONDS, purge).run()
        results = collect_results(result_queue, PROCESSES, total_timeout=SECONDS + 60)
        stop_processes(procs)
        assert [payload for kind, payload in results if kind == 'error'] == []
        assert len(results) == PROCESSES
        report = _merge([own] + [payload for _, payload in results])
        counter = app.cache.get('counter')
        warnings = app.cache.check()
        directory_db = os.path.join(directory, 'cache.db')
        app.cache.close()
    db = sqlite3.connect(directory_db)
    integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
    db.close()
    print(f'workers={report["workers"]} ops={sum(report["ops"].values())} {dict(report["ops"])}')
    print(f'opened connections in the main process: {len(connections.opened)}')
    print(f'counter={counter} increments={report["counter"]} lost locks={len(report["lost"])}')
    _show(report, 'all')
    if purge:
        _show(_after(report, max(report['purges'])), 'tail')
    return report, counter, warnings, integrity, connections


@pytest.mark.parametrize('purge', [False, True], ids=['steady', 'purge'])
def test_stress_runs_without_errors_or_wrong_values(tmp_path, monkeypatch, purge):
    """
    Threads in three processes run random operations on one cache, once
    steady and once with purges in between. Both runs end without errors
    or wrong values, with a sound database and no open connection; the
    steady run also keeps every count, lock and limit.
    """
    report, counter, warnings, integrity, connections = _stress(str(tmp_path), monkeypatch, purge)
    # no failed operation, no wrong value, a healthy database, no open connection
    assert report['errors'] == []
    assert report['wrong'] == []
    assert warnings == []
    assert integrity == 'ok'
    assert connections.unclosed == []
    if purge:
        # purges reset the locks, running holders then raise the limits for a while
        assert report['ops']['purge.purge'] > 0
        return
    # without purges every increment counts, no lock is lost and every limit holds
    assert counter == report['counter']
    assert report['lost'] == []
    for name, count in TEMPERS.items():
        assert len(report['tempers'][name]) > 0
        assert max_overlap(report['tempers'][name]) <= count
    assert max_overlap(report['mutex']) == 1
    for name, (count, per_seconds, _) in THROTTLES.items():
        assert len(report['throttles'][name]) > 0
        assert _throttle_excess(report['throttles'][name], count, per_seconds) <= 0
