"""
Pre-fork process model.

A faithful translation of midi-smtp-server's master/worker handling (the
``@pre_fork`` / ``@workers`` / ``fork`` / ``Process.waitpid`` logic on
``MidiSmtpServer::Server``) into a small, framework-neutral helper.

The model, like midi's, is: a *master* process forks ``pre_fork`` *worker*
processes, and only the workers serve. The master itself does not serve; it
forwards the stop signal (SIGINT/SIGTERM) to every worker and reaps them with
``waitpid`` so no worker is left orphaned or turned into a zombie. Pre-fork is
only active for ``count > 1`` (midi: ``pre_fork?``); otherwise a single process
serves directly.

On Linux each worker additionally arms ``PR_SET_PDEATHSIG`` so it terminates
itself if the master ever dies unexpectedly (a hard crash the master cannot
forward from). This is the only Linux-specific part; everything else runs on
any POSIX platform that provides ``os.fork``.
"""

import os
import sys
import ctypes
import signal


#: ``prctl`` option number for the parent-death signal (Linux ``<sys/prctl.h>``)
_PR_SET_PDEATHSIG = 1

#: Signals that ask the master to stop and that it forwards to every worker
_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def set_pdeathsig(sig=signal.SIGTERM):
    """
    Arm the parent-death signal for the calling process (Linux only).

    ### Notes

    - On Linux, asks the kernel (```prctl(PR_SET_PDEATHSIG, sig)```) to send
        ```sig``` to this process when its parent dies, so an orphaned worker
        does not linger if the master crashes without forwarding a stop
    - A no-op on non-Linux platforms and best-effort on error; the master's
        explicit signalling stays the primary shutdown path
    - ```ctypes.CDLL(None)``` resolves ```prctl``` from the C library already
        loaded into the running process, so no library filename is hardcoded
        (```libc.so``` is often a linker script and will not ```dlopen```)

    ### Args

    - **sig** (int): Signal to receive on parent death (default ```SIGTERM```)

    ### Returns

    - **bool**: True if the signal was armed, False on non-Linux or on error

    """
    if not sys.platform.startswith('linux'):
        return False
    try:
        ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_PDEATHSIG, sig, 0, 0, 0)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def is_prefork(count):
    """
    Return whether pre-fork is active for ```count``` (midi: ```pre_fork?```).

    ### Notes

    : Mirrors midi exactly: forking happens only for ```count > 1```; a value
        of 0 or 1 means a single process serves directly.

    ### Args

    - **count** (int): The configured ```pre_fork``` value

    ### Returns

    - **bool**: True if that many workers should be forked and supervised

    """
    return int(count) > 1


def supervise_workers(child_pids, stop_signals=_STOP_SIGNALS):
    """
    Master supervision loop: forward the stop signal, then reap every worker.

    ### Notes

    - Installs a handler for each stop signal that relays SIGTERM to all
        workers (midi: ```@workers.each { Process.kill(:TERM, pid) }```), then
        blocks in ```os.wait``` until every worker has exited (midi:
        ```@workers.each { Process.waitpid(pid) }```) so none is left zombied
    - Already-gone workers are ignored (```ProcessLookupError```); waiting is
        resumed across an interrupted syscall
    - The previous signal handlers are restored on return

    ### Args

    - **child_pids** (list): PIDs of the forked workers to supervise
    - **stop_signals** (tuple): Signals that trigger the forward-and-reap

    """
    def _forward(_signum, _frame):
        for pid in child_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    previous = {}
    for s in stop_signals:
        previous[s] = signal.signal(s, _forward)
    try:
        remaining = set(child_pids)
        while remaining:
            try:
                pid, _status = os.wait()
                remaining.discard(pid)
            except ChildProcessError:
                break
            except InterruptedError:
                continue
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def run_prefork(worker, count, parent_death_signal=signal.SIGTERM):
    """
    Run ```worker``` under midi's master/worker pre-fork model.

    ### Notes

    - With ```count <= 1``` no fork happens; ```worker()``` runs in this
        process (single master, midi's default)
    - With ```count > 1``` a master forks ```count``` workers and supervises
        them; each worker arms ```set_pdeathsig``` (Linux) plus a race guard
        against the master dying between ```fork``` and ```prctl```, then runs
        ```worker()``` and exits via ```os._exit```; the master forwards
        SIGINT/SIGTERM to every worker and reaps them (see
        ```supervise_workers```)
    - Blocking; returns in the master only after all workers have exited

    ### Args

    - **worker** (callable): The per-process serve routine (e.g. an
        ```asyncio.run(...)``` wrapper); it should block until stopped
    - **count** (int): The configured ```pre_fork``` value
    - **parent_death_signal** (int): Signal a worker requests on master death

    ### Returns

    - **list**: The supervised worker PIDs (empty when running single-process)

    """
    if not is_prefork(count):
        worker()
        return []
    master_pid = os.getpid()
    child_pids = []
    for _ in range(int(count)):
        pid = os.fork()
        if pid == 0:
            # ===== worker =====
            set_pdeathsig(parent_death_signal)
            # race guard: if the master already died between fork() and the
            # prctl() call, the parent-death signal can never fire -- bail now
            if os.getppid() != master_pid:
                os._exit(0)
            try:
                worker()
            finally:
                os._exit(0)
        child_pids.append(pid)
    # ===== master: supervise and reap, does not serve =====
    supervise_workers(child_pids)
    return child_pids
