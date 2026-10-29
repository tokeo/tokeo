"""
Tests for tokeo.core.utils.timelimit: ```@timelimit``` aborts a normal
function by ```TokeoTimelimitExceeded```, also through ```except Exception```
and in a thread pool, keeps nested limits apart and cancels a coroutine at its
next await. The rare cases are stress tests of many runs, only with
```STRESSTESTS=1```: calls and errors that end right at the limit, nested
limits that run off together and many threads at once never leave a stray
exception or a timer thread behind.
"""

import time
import random
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tokeo.core.exc import TokeoBaseException
from tokeo.core.utils.timelimit import timelimit, TokeoTimelimitExceeded
from tests.utils import stresstest


def _busy(seconds):
    # python steps only, so the exception arrives at once
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pass


def _stray_exception():
    # python steps after a call, a stray exception of its limit shows up here
    try:
        _busy(0.01)
        return False
    except TokeoTimelimitExceeded:
        return True


def test_exceeded_is_a_base_exception_and_no_exception():
    """TokeoTimelimitExceeded is a TokeoBaseException, but no Exception."""
    assert issubclass(TokeoTimelimitExceeded, TokeoBaseException)
    assert not issubclass(TokeoTimelimitExceeded, Exception)


@pytest.mark.parametrize('seconds', [0, -1, True, '1', None], ids=['zero', 'negative', 'bool', 'str', 'none'])
def test_seconds_must_be_a_number_above_0(seconds):
    """timelimit refuses seconds that are no number above 0."""
    with pytest.raises(ValueError, match='seconds must be a number above 0'):
        timelimit(seconds)


def test_call_within_its_limit_returns_and_nothing_arrives_later():
    """A call within its limit returns, no exception follows, no timer stays."""

    @timelimit(0.2)
    def job():
        return 'ran'

    assert job() == 'ran'
    _busy(0.4)
    assert threading.active_count() == 1


def test_call_past_its_limit_is_aborted_with_a_message():
    """A call past its limit of 0.2 s ends within a few milliseconds."""

    @timelimit(0.2)
    def job():
        _busy(10)

    started = time.monotonic()
    with pytest.raises(TokeoTimelimitExceeded, match='job exceeded its time limit of 0.2 s'):
        job()
    assert 0.2 <= time.monotonic() - started < 0.5
    assert not _stray_exception()


def test_except_exception_in_the_call_does_not_swallow_it():
    """A loop with except Exception around python steps is still aborted."""

    @timelimit(0.2)
    def job():
        while True:
            try:
                _busy(0.05)
            except Exception:
                pass

    with pytest.raises(TokeoTimelimitExceeded):
        job()


def test_except_base_exception_in_the_call_swallows_it():
    """A call that swallows BaseException runs to its end, the limit is lost."""

    @timelimit(0.1)
    def job():
        try:
            _busy(0.3)
        except BaseException:
            pass
        return 'ran'

    assert job() == 'ran'
    assert not _stray_exception()


def test_finally_of_the_call_runs():
    """The finally block of an aborted call runs."""
    cleaned = []

    @timelimit(0.2)
    def job():
        try:
            _busy(10)
        finally:
            cleaned.append(True)

    with pytest.raises(TokeoTimelimitExceeded):
        job()
    assert cleaned == [True]


def test_outer_limit_goes_through_an_inner_one():
    """The limit of the outer call passes the inner call with its message."""

    @timelimit(10)
    def inner():
        _busy(10)

    @timelimit(0.2)
    def outer():
        inner()

    with pytest.raises(TokeoTimelimitExceeded, match='outer exceeded'):
        outer()


def test_inner_limit_can_be_caught_by_the_outer_call():
    """An outer call catches the abort of its inner call and goes on."""

    @timelimit(0.2)
    def inner():
        _busy(10)

    @timelimit(10)
    def outer():
        try:
            inner()
        except TokeoTimelimitExceeded as err:
            return str(err)

    assert 'inner exceeded its time limit of 0.2 s' in outer()
    assert not _stray_exception()


def test_call_in_a_thread_pool_is_aborted():
    """A call in a thread pool, as in the scheduler, is aborted."""

    @timelimit(0.2)
    def job():
        _busy(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(job)
        assert isinstance(future.exception(timeout=5), TokeoTimelimitExceeded)


def test_timer_that_cannot_start_fails_the_call(monkeypatch):
    """Without a free thread the call fails at once and does not run."""
    runs = []

    def no_thread(self):
        raise RuntimeError("can't start new thread")

    @timelimit(1)
    def job():
        runs.append(True)

    monkeypatch.setattr(threading.Timer, 'start', no_thread)
    with pytest.raises(RuntimeError, match="can't start new thread"):
        job()
    assert runs == []


def test_coroutine_is_cancelled_at_its_next_await():
    """A coroutine past its limit is cancelled at once."""

    @timelimit(0.2)
    async def page():
        await asyncio.sleep(10)

    started = time.monotonic()
    with pytest.raises(TokeoTimelimitExceeded, match='page exceeded its time limit of 0.2 s'):
        asyncio.run(page())
    assert time.monotonic() - started < 0.5


def test_coroutine_within_its_limit_and_its_own_timeout_error():
    """A coroutine returns within its limit, its own TimeoutError goes on."""

    @timelimit(1)
    async def page():
        return 'ran'

    @timelimit(1)
    async def failing():
        raise TimeoutError('own')

    assert asyncio.run(page()) == 'ran'
    with pytest.raises(TimeoutError, match='own'):
        asyncio.run(failing())


# --------------------------------------------------------------------------------------
# rare cases: things that happen at the same moment, stress tests
# --------------------------------------------------------------------------------------


@stresstest
def test_calls_ending_right_at_their_limit_leave_nothing_behind():
    """Of 200 calls ending around their limit each runs or aborts, none leaks."""

    @timelimit(0.02)
    def job(duration):
        _busy(duration)
        return 'ran'

    outcomes = set()
    for _ in range(200):
        try:
            outcomes.add(job(random.uniform(0.0, 0.05)))
        except TokeoTimelimitExceeded:
            outcomes.add('aborted')
        assert not _stray_exception()
    assert outcomes == {'ran', 'aborted'}
    assert threading.active_count() == 1


@stresstest
def test_errors_raised_right_at_the_limit_leave_nothing_behind():
    """Of 200 calls failing around their limit each fails once, none leaks."""

    @timelimit(0.02)
    def job(duration):
        _busy(duration)
        raise ValueError('own')

    outcomes = set()
    for _ in range(200):
        try:
            job(random.uniform(0.0, 0.05))
        except ValueError:
            outcomes.add('own error')
        except TokeoTimelimitExceeded:
            outcomes.add('aborted')
        assert not _stray_exception()
    assert outcomes == {'own error', 'aborted'}


@stresstest
def test_nested_limits_running_off_together_do_not_lose_the_outer_one():
    """Of 200 nested calls with the same limit the outer one never runs on."""

    @timelimit(0.02)
    def inner(duration):
        _busy(duration)

    @timelimit(0.02)
    def outer(duration):
        inner(duration)
        # far past the limit of the outer call
        _busy(1)
        return 'ran on'

    for _ in range(200):
        started = time.monotonic()
        with pytest.raises(TokeoTimelimitExceeded):
            outer(random.uniform(0.0, 0.05))
        assert time.monotonic() - started < 0.5
        assert not _stray_exception()


@stresstest
def test_many_threads_running_off_at_once_are_all_aborted():
    """50 busy threads with the same limit are all aborted, no timer stays."""
    aborted = []
    lock = threading.Lock()

    @timelimit(0.2)
    def job():
        _busy(30)

    def run():
        try:
            job()
        except TokeoTimelimitExceeded:
            with lock:
                aborted.append(True)

    threads = [threading.Thread(target=run) for _ in range(50)]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(aborted) == 50
    assert time.monotonic() - started < 10
    assert threading.active_count() == 1
