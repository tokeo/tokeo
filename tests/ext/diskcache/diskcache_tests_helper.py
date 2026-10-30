"""
Shared parts of the tests of tokeo.ext.diskcache: the test app on a fixed
directory, the recorder of the sqlite connections and the tools for threads
and processes.
"""

import time
import sqlite3
import multiprocessing as mp
from queue import Empty
from concurrent.futures import ThreadPoolExecutor

from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest


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
    """A test app with the diskcache in the given directory and further settings."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.diskcache',
        ]

    def __init__(self, directory, settings=None, **kw):
        # pin the diskcache to a fixed directory so that threads and separate
        # processes coordinate through the very same cache database
        config = init_defaults('diskcache')
        config['diskcache'].update(settings or {}, directory=directory)
        super().__init__(config_defaults=config, **kw)


class Connections:
    """Records the sqlite connections diskcache opens and which got closed."""

    def __init__(self, monkeypatch):
        self.opened = []
        connect = sqlite3.connect
        connections = self

        class Connection(sqlite3.Connection):

            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self.closed = False
                connections.opened.append(self)

            def close(self):
                self.closed = True
                super().close()

        monkeypatch.setattr('diskcache.core.sqlite3.connect', lambda *args, **kw: connect(*args, factory=Connection, **kw))

    @property
    def unclosed(self):
        return [con for con in self.opened if not con.closed]


def in_threads(func, workers=4):
    # run func once in each of the workers threads of a pool, return the results
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda _: func(), range(workers)))


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
