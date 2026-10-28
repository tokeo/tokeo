"""
Tests for tokeo.ext.smtpd: settings mapping, topology planning, shared
limits across processes and the termination guarantees with real processes.

The termination guarantees are process properties (exit code, port release,
SIGKILL survival) and are observed from outside: ``smtpd_tests_serve_probe.py``
runs a real ``TokeoTest`` Cement app in its own process group; the tests
detect readiness through the SMTP banner on the port and cleanup through
the process group and the port becoming bindable again.
"""

import ssl
import asyncio
import hashlib
import datetime
import multiprocessing
import os
import re
import signal
import smtplib
import socket
import subprocess
import sys
import time

import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tokeo.core.smtpd import GlobalLimits
from tokeo.ext.smtpd import (
    TokeoSmtpdError,
    SharedGlobalLimits,
    _resolve_events_handler,
    _plan,
)
from tests.utils import ROOT, wait_until, wait_process_group_ended
from tests.ext.smtpd.smtpd_tests_helper import SmtpdTest

needs_pdeathsig = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='the parent-death signal is Linux-only')

# --- unit: handler resolving -----------------------------------------------------


def test_resolve_events_handler_imports_and_rejects():
    """A dotted path imports the handler, anything else raises TokeoSmtpdError."""
    from tokeo.ext.smtpd import TokeoSmtpdEvents

    assert _resolve_events_handler('tokeo.ext.smtpd.TokeoSmtpdEvents') is TokeoSmtpdEvents
    with pytest.raises(TokeoSmtpdError):
        _resolve_events_handler('NoDots')
    with pytest.raises(TokeoSmtpdError):
        _resolve_events_handler('tokeo.core.smtpd.events.NoSuchClass')
    with pytest.raises(TokeoSmtpdError):
        # importable, but not a TokeoSmtpdEvents subclass: the core class
        _resolve_events_handler('tokeo.core.smtpd.events.SmtpdEvents')


def test_events_handler_carries_the_app():
    """TokeoSmtpdEvents keeps the app and the options."""
    from tokeo.ext.smtpd import TokeoSmtpdEvents

    events_handler = TokeoSmtpdEvents('app-sentinel', options={'answer': 42})
    assert events_handler.app == 'app-sentinel'
    assert events_handler.options == {'answer': 42}


# --- unit: topology planning ----------------------------------------------------


def _svc(name, pre_fork=0):
    svc = {'name': name, 'settings': {}}
    if pre_fork:
        svc['settings']['pre_fork'] = pre_fork
    return svc


def test_plan_single_service_stays_in_main_process():
    """One service with pre_fork up to 1 runs in the main process."""
    for pre_fork in (0, 1):
        plan = _plan([_svc('mx1', pre_fork)], has_global_limit=True)
        assert plan['inloop'] == [_svc('mx1', pre_fork)]
        assert plan['forks'] == [] and plan['shared'] is False


def test_plan_single_service_prefork_main_is_worker_one():
    """One service with pre_fork 3 runs in main plus 2 forks on one port."""
    plan = _plan([_svc('mx1', 3)], has_global_limit=True)
    assert plan['inloop'][0]['name'] == 'mx1'  # main process serves
    assert plan['forks'] == [(_svc('mx1', 3), 2)]  # plus N-1 workers
    assert plan['reuse'] == {'mx1'}
    assert plan['shared'] is True


def test_plan_multi_services_rules():
    """Of several services only pre_fork 0 stays in main, all others fork."""
    services = [_svc('mx1', 5), _svc('mx2'), _svc('mx3', 1)]
    plan = _plan(services, has_global_limit=True)
    assert [svc['name'] for svc in plan['inloop']] == ['mx2']
    assert [(svc['name'], count) for svc, count in plan['forks']] == [('mx1', 5), ('mx3', 1)]
    assert plan['reuse'] == {'mx1'}  # only groups > 1 share a port
    assert plan['shared'] is True
    # all services forked: the master only supervises
    plan = _plan([_svc('mx1', 2), _svc('mx2', 1)], has_global_limit=False)
    assert plan['inloop'] == [] and plan['shared'] is False


# --- unit: shared limits ---------------------------------------------------------


def test_shared_limits_count_across_processes():
    """SharedGlobalLimits counts admits from a forked process."""
    limits = SharedGlobalLimits(max_processings=5, max_connections=7)
    assert isinstance(limits, GlobalLimits)
    assert limits.max_processings == 5 and limits.max_connections == 7

    def child(shared):
        shared.admit()
        shared.admit()
        shared.begin_processing()

    # the extension inherits the counters via os.fork; mirror that in the
    # test (the spawn default on macOS would also have to pickle 'child')
    proc = multiprocessing.get_context('fork').Process(target=child, args=(limits,))
    proc.start()
    proc.join(5)
    assert limits.active == 2 and limits.active_processings == 1
    limits.release()
    limits.end_processing()
    limits.release()
    limits.release()  # floor at 0
    assert limits.active == 0 and limits.active_processings == 0


def test_shared_limits_slot_wait_recheck_interval():
    """slot_wait returns by its poll within 0.5 s without an event."""
    limits = SharedGlobalLimits(max_processings=1)

    async def go():
        limits.slot_free.clear()
        start = time.perf_counter()
        await limits.slot_wait()  # nobody sets the event
        return time.perf_counter() - start

    assert asyncio.run(go()) < 0.5  # returns via the poll


# --- cement wiring ---------------------------------------------------------------


def test_extension_loads_into_cement_app():
    """The extension loads its services and builds a server for each."""
    config = dict(
        smtpd=dict(
            services=[
                {
                    'name': 'mx-a',
                    'events_handler': 'tokeo.ext.smtpd.TokeoSmtpdEvents',
                    'listeners': [{'host': '127.0.0.1', 'port': 2525}],
                }
            ]
        )
    )
    with SmtpdTest(config_defaults=config) as app:
        assert app.smtpd is not None
        assert [svc['name'] for svc in app.smtpd.services()] == ['mx-a']
        with pytest.raises(TokeoSmtpdError):
            app.smtpd.services(['nope'])
        with pytest.raises(TokeoSmtpdError):
            app.smtpd.services(['mx-a', 'mx-a'])  # named twice must not start twice
        app.smtpd.list()  # must run without raising
        # the listener hosts reach the server (feeds the self-signed CN/SAN)
        server = app.smtpd._build_server(app.smtpd.services()[0], None)
        assert server.hosts == ['127.0.0.1']
        # the global smtpd config sizes the session id for every service
        assert server.session_id_bytes == 8
        # the default on_logging_event writes to the application log
        assert server.events_handler._logger_protected is app.log


@pytest.mark.parametrize(
    'svc, reason',
    [
        (dict(events_handler='tokeo.ext.smtpd.TokeoSmtpdEvents', settings=dict(encrypt_mode='TLS_NOPE')), 'encrypt_mode must be one of'),
        (dict(events_handler='NoDots'), 'events_handler needs a dotted path'),
    ],
    ids=['encrypt_mode', 'events_handler'],
)
def test_build_server_names_the_service(svc, reason):
    """A service that cannot be built raises TokeoSmtpdError naming it."""
    with SmtpdTest() as app:
        with pytest.raises(TokeoSmtpdError, match=f"^smtpd: service 'mx-x': {reason}"):
            app.smtpd._build_server(dict(svc, name='mx-x', listeners=[]), None)


def _build(svc):
    """Build the server of one service of a test app."""
    with SmtpdTest() as app:
        server = app.smtpd._build_server(dict(svc, name='mx-x', listeners=[], events_handler='tokeo.ext.smtpd.TokeoSmtpdEvents'), None)
        return app, server


def test_build_server_hands_the_options_to_the_handler():
    """The options of a service reach its events handler (ctx.options is gone)."""
    _, server = _build({'options': {'tag': 'opt-42'}})
    assert server.events_handler.options == {'tag': 'opt-42'}


def test_build_server_hands_the_settings_to_the_server():
    """The settings of a service reach its server."""
    _, server = _build({'settings': {'io_session_timeout': 60, 'crlf_data_mode': 'CRLF_DATA_STRICT', 'pre_fork': 2}})
    assert (server.io_session_timeout, server.crlf_data_mode.name) == (60, 'CRLF_DATA_STRICT')


def test_build_server_leaves_out_logger_severity():
    """A logger_severity of a service is left out, the server logs to app.log."""
    app, server = _build({'settings': {'logger_severity': 'DEBUG'}})
    assert server.events_handler._logger_protected is app.log


# --- process tests: real termination behaviour ----------------------------------


PROBE = 'tests.ext.smtpd.smtpd_tests_serve_probe'


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _service(name, port, pre_fork=0):
    svc = {
        'name': name,
        'events_handler': 'tokeo.ext.smtpd.TokeoSmtpdEvents',
        'listeners': [{'host': '127.0.0.1', 'port': port}],
    }
    if pre_fork:
        svc['settings'] = {'pre_fork': pre_fork}
    return svc


def _spawn(tmp_path, config, names=None):
    import json

    # an own session = an own process group: master and all its workers,
    # observable from outside via os.killpg; all output goes to a file so a
    # failing probe can report its actual error instead of a blind timeout
    probe_log = tmp_path / 'smtpd-probe.log'
    with open(probe_log, 'w') as log:
        proc = subprocess.Popen(
            [sys.executable, '-m', PROBE, json.dumps(config), json.dumps(names)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=dict(os.environ),
            cwd=ROOT,
        )
    proc.probe_log = probe_log
    return proc


def _wait_exit(proc, timeout=8):
    """Wait for the probe; a hang reports the probe's own story."""
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AssertionError(f'probe still running after {timeout}s:\n{_probe_log(proc, lines=30)}')


def _probe_log(proc, lines=15):
    with open(proc.probe_log) as f:
        tail = f.readlines()[-lines:]
    return ''.join(tail).strip() or '(no output)'


# test rules exception: a wait loop of its own, it tells why the probe did not serve
def _wait_banner(proc, port, timeout=8.0):
    """Readiness: the SMTP banner answers on the port."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f'probe exited rc={proc.returncode} before serving:\n{_probe_log(proc)}')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1) as sock:
                if sock.recv(4).startswith(b'220'):
                    return
        except OSError:
            pass
        time.sleep(0.05)
    raise AssertionError(f'no smtp banner on port {port}:\n{_probe_log(proc)}')


def _child_pids(pid):
    """The forked workers of the master (Linux process tree)."""
    with open(f'/proc/{pid}/task/{pid}/children') as f:
        return [int(child) for child in f.read().split()]


def _ehlo_ok(port):
    client = smtplib.SMTP('127.0.0.1', port, timeout=5)
    code, _ = client.ehlo('probe')
    client.quit()
    assert code == 250


def _assert_port_free(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))


def test_term_single_service_in_main_process(tmp_path):
    """SIGTERM ends one service in main with 0, the port is free again."""
    port = _free_port()
    proc = _spawn(tmp_path, {'services': [_service('mx1', port)]}, names=None)
    try:
        _wait_banner(proc, port)
        _ehlo_ok(port)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=8) == 0
        assert wait_process_group_ended(proc.pid)
        _assert_port_free(port)
    finally:
        proc.kill()


def test_term_single_service_prefork_group(tmp_path):
    """SIGTERM ends main and its 2 forks with 0, the port is free again."""
    port = _free_port()
    proc = _spawn(tmp_path, {'services': [_service('mx1', port, pre_fork=3)]}, names=None)
    try:
        _wait_banner(proc, port)
        if sys.platform.startswith('linux'):
            assert len(_child_pids(proc.pid)) == 2
        _ehlo_ok(port)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=8) == 0
        assert wait_process_group_ended(proc.pid)
        _assert_port_free(port)
    finally:
        proc.kill()


@needs_pdeathsig
def test_kill_master_takes_workers_down(tmp_path):
    """After SIGKILL of the master its workers end by the parent death signal."""
    port = _free_port()
    proc = _spawn(tmp_path, {'services': [_service('mx1', port, pre_fork=3)]}, names=None)
    try:
        _wait_banner(proc, port)
        assert len(_child_pids(proc.pid)) == 2
        proc.kill()
        proc.wait(timeout=8)
        assert wait_process_group_ended(proc.pid)
    finally:
        proc.kill()


def test_term_mixed_services_with_shared_limits(tmp_path):
    """One SIGTERM ends forked and main services with shared limits."""
    port_a, port_b = _free_port(), _free_port()
    config = {
        'max_connections': 10,
        'services': [_service('mx1', port_a, pre_fork=2), _service('mx2', port_b)],
    }
    proc = _spawn(tmp_path, config, names=None)
    try:
        _wait_banner(proc, port_a)
        _wait_banner(proc, port_b)
        _ehlo_ok(port_a)
        _ehlo_ok(port_b)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=8) == 0
        assert wait_process_group_ended(proc.pid)
        _assert_port_free(port_a)
        _assert_port_free(port_b)
    finally:
        proc.kill()


def _broken_service(name, pre_fork=0):
    """A service whose listener cannot bind (TEST-NET address)."""
    svc = {
        'name': name,
        'events_handler': 'tokeo.ext.smtpd.TokeoSmtpdEvents',
        'listeners': [{'host': '203.0.113.1', 'port': 2525}],
    }
    if pre_fork:
        svc['settings'] = {'pre_fork': pre_fork}
    return svc


def test_failed_service_start_aborts_and_stops_started_ones(tmp_path):
    """A service that cannot bind ends the run with an error, ports free."""
    port_a = _free_port()
    proc = _spawn(tmp_path, {'services': [_service('mx1', port_a), _broken_service('mx2')]}, names=None)
    try:
        assert _wait_exit(proc) != 0
        assert wait_process_group_ended(proc.pid)
        _assert_port_free(port_a)
    finally:
        proc.kill()


def test_failed_start_terminates_forked_workers(tmp_path):
    """A failed start in main also ends the forked workers."""
    port_a = _free_port()
    proc = _spawn(tmp_path, {'services': [_service('mx1', port_a, pre_fork=2), _broken_service('mx2')]}, names=None)
    try:
        assert _wait_exit(proc) != 0
        assert wait_process_group_ended(proc.pid)
        _assert_port_free(port_a)
    finally:
        proc.kill()


def test_broken_config_of_a_worker_stops_before_the_fork(tmp_path):
    """A worker service with a broken config ends the start before any fork."""
    port_a, port_b = _free_port(), _free_port()
    broken = _service('mx2', port_b, pre_fork=1)
    broken['settings']['encrypt_mode'] = 'TLS_NOPE'
    proc = _spawn(tmp_path, {'services': [_service('mx1', port_a), broken]}, names=None)
    try:
        assert _wait_exit(proc) != 0
        assert wait_process_group_ended(proc.pid)
        log = proc.probe_log.read_text()
        assert "smtpd: service 'mx2': encrypt_mode must be one of" in log
        assert 'forked workers' not in log
        _assert_port_free(port_a)
    finally:
        proc.kill()


def test_failed_worker_stops_all_services(tmp_path):
    """A worker that cannot bind logs why and ends all services, ports free."""
    port_a = _free_port()
    proc = _spawn(tmp_path, {'services': [_service('mx1', port_a), _broken_service('mx2', pre_fork=1)]}, names=None)
    try:
        assert _wait_exit(proc) != 0
        assert wait_process_group_ended(proc.pid)
        log = proc.probe_log.read_text()
        assert "of service 'mx2' failed: could not bind" in log
        assert "of service 'mx2' ended with 1, all services stopped" in log
        _assert_port_free(port_a)
    finally:
        proc.kill()


# --- process tests: services in the forked workers ------------------------------


def _tagged_service(name, port, pre_fork, settings=None):
    """A service whose handler answers EHLO with its tag and pid."""
    svc = _service(name, port, pre_fork=pre_fork)
    svc['events_handler'] = 'tests.ext.smtpd.smtpd_tests_helper.TaggedSmtpdEvents'
    svc['options'] = {'tag': 'opt-42'}
    svc['settings'].update(settings or {})
    return svc


def _kill_group(proc):
    """End the probe and all its workers, also when a test failed."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=8)


def _open_dialogs(port, count):
    """Open count connections and read their banner."""
    socks = []
    for _ in range(count):
        sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        assert sock.recv(1024).startswith(b'220')
        socks.append(sock)
    return socks


def test_prefork_workers_hand_the_options_to_their_handler(tmp_path):
    """Every process of a group of 3 answers EHLO with the tag of the options."""
    port = _free_port()
    proc = _spawn(tmp_path, {'services': [_tagged_service('mx1', port, pre_fork=3)]}, names=None)
    try:
        _wait_banner(proc, port)
        pids = set()
        for _ in range(40):
            client = smtplib.SMTP('127.0.0.1', port, timeout=5)
            code, msg = client.ehlo('probe')
            client.quit()
            first = msg.decode().splitlines()[0]
            assert code == 250 and first.startswith('opt-42 pid ')
            pids.add(int(first.rsplit(' ', 1)[1]))
        # SO_REUSEPORT spreads 40 connections over more than one process
        assert len(pids) > 1
        if sys.platform.startswith('linux'):
            assert pids <= {proc.pid, *_child_pids(proc.pid)}
    finally:
        _kill_group(proc)


def test_prefork_workers_apply_the_settings(tmp_path):
    """Open dialogs of a group of 3 end by 421 after io_session_timeout of 1 s."""
    port = _free_port()
    proc = _spawn(tmp_path, {'services': [_tagged_service('mx1', port, pre_fork=3, settings={'io_session_timeout': 1})]}, names=None)
    try:
        _wait_banner(proc, port)
        socks = _open_dialogs(port, 6)
        for sock in socks:
            assert sock.recv(1024).startswith(b'421')
            sock.close()
    finally:
        _kill_group(proc)


def test_term_prefork_group_ends_open_dialogs_with_421(tmp_path):
    """SIGTERM ends the waiting dialogs of all processes of a group of 3 by 421."""
    port = _free_port()
    proc = _spawn(tmp_path, {'services': [_tagged_service('mx1', port, pre_fork=3)]}, names=None)
    try:
        _wait_banner(proc, port)
        socks = _open_dialogs(port, 6)
        proc.send_signal(signal.SIGTERM)
        for sock in socks:
            assert sock.recv(1024).startswith(b'421')
            sock.close()
        assert proc.wait(timeout=8) == 0
        assert wait_process_group_ended(proc.pid)
    finally:
        _kill_group(proc)


@needs_pdeathsig
def test_prefork_logs_the_started_service_of_every_process(tmp_path):
    """The master logs its workers, every process its started service and pid."""
    port = _free_port()
    proc = _spawn(tmp_path, {'services': [_tagged_service('mx1', port, pre_fork=3)]}, names=None)
    try:
        _wait_banner(proc, port)
        pids = [proc.pid, *_child_pids(proc.pid)]
        assert len(pids) == 3
        started = [f"smtpd: service 'mx1' started (pid {pid})" for pid in pids]
        assert wait_until(lambda: all(line in proc.probe_log.read_text() for line in started))
        forked = re.search(r'smtpd: forked workers \[([0-9, ]+)\]', proc.probe_log.read_text())
        assert {int(pid) for pid in forked.group(1).split(',')} == set(pids[1:])
    finally:
        _kill_group(proc)


@pytest.fixture(scope='module')
def cert_files(tmp_path_factory):
    """A self-signed certificate and its key as files, with its SHA256."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    tmp = tmp_path_factory.mktemp('certs')
    (tmp / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    (tmp / 'key.pem').write_bytes(key_pem)
    return str(tmp / 'cert.pem'), str(tmp / 'key.pem'), hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def test_prefork_workers_present_the_configured_certificate(tmp_path, cert_files):
    """All processes of a group of 3 present the certificate of tls_cert_path."""
    cert_path, key_path, fingerprint = cert_files
    port = _free_port()
    settings = {'encrypt_mode': 'TLS_OPTIONAL', 'tls_cert_path': cert_path, 'tls_key_path': key_path}
    proc = _spawn(tmp_path, {'services': [_tagged_service('mx1', port, pre_fork=3, settings=settings)]}, names=None)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        _wait_banner(proc, port)
        pids, fingerprints = set(), set()
        for _ in range(40):
            client = smtplib.SMTP('127.0.0.1', port, timeout=5)
            _, msg = client.ehlo('probe')
            pids.add(msg.decode().splitlines()[0].rsplit(' ', 1)[1])
            client.starttls(context=context)
            fingerprints.add(hashlib.sha256(client.sock.getpeercert(binary_form=True)).hexdigest())
            client.quit()
        # more than one process served, all with the one certificate
        assert len(pids) > 1
        assert fingerprints == {fingerprint}
    finally:
        _kill_group(proc)
