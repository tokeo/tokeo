"""
Tests for tokeo.ext.smtpd: server options mapping, topology planning, shared
limits across processes and the termination guarantees with real processes.

The termination guarantees are process properties (exit code, port release,
SIGKILL survival) and are observed from outside: ```smtpd_tests_serve_probe.py```
runs a real ```TokeoTest``` Cement app in its own process group; the tests
detect readiness through the SMTP banner on the port and cleanup through
the process group and the port becoming bindable again.
"""

import ssl
import asyncio
import hashlib
import datetime
import multiprocessing
import re
import signal
import smtplib
import socket
import sys
import time
import json

import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tokeo.core.smtpd import GlobalLimits
from tokeo.core.utils.modules import TokeoModulesLoadError
from tokeo.ext.smtpd import (
    TokeoSmtpdError,
    SharedGlobalLimits,
    _resolve_events_handler,
    _build_plan,
)
from tests.utils import free_port, isolated, wait_until, wait_process_group_ended
from tests.utils.process import SubProcess
from tests.ext.smtpd.smtpd_tests_helper import SmtpdTest

needs_pdeathsig = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='the parent-death signal is Linux-only')

# ---- unit: handler resolving ------------------------------------------------


def test_resolve_events_handler_imports_the_class():
    """module:class imports the handler class."""
    from tokeo.ext.smtpd import TokeoSmtpdEvents

    assert _resolve_events_handler('tokeo.ext.smtpd:TokeoSmtpdEvents') is TokeoSmtpdEvents


def test_resolve_events_handler_rejects():
    """A path that does not resolve or no TokeoSmtpdEvents subclass raises."""
    with pytest.raises(TokeoModulesLoadError, match='must be set as module:attr'):
        _resolve_events_handler('NoDots')
    with pytest.raises(TokeoModulesLoadError, match='could not be found'):
        _resolve_events_handler('tokeo.core.smtpd.events:NoSuchClass')
    with pytest.raises(TokeoSmtpdError, match='must subclass'):
        # importable, but not a TokeoSmtpdEvents subclass: the core class
        _resolve_events_handler('tokeo.core.smtpd.events:SmtpdEvents')


def test_events_handler_carries_the_app():
    """TokeoSmtpdEvents keeps the app and the options."""
    from tokeo.ext.smtpd import TokeoSmtpdEvents

    events_handler = TokeoSmtpdEvents('app-sentinel', options={'answer': 42})
    assert events_handler.app == 'app-sentinel'
    assert events_handler.options == {'answer': 42}


# ---- unit: topology planning ------------------------------------------------


def _make_svc(name, pre_fork=0):
    svc = {'name': name, 'server_options': {}}
    if pre_fork:
        svc['server_options']['pre_fork'] = pre_fork
    return svc


def test_plan_single_service_stays_in_main_process():
    """One service with pre_fork up to 1 runs in the main process."""
    for pre_fork in (0, 1):
        plan = _build_plan([_make_svc('mx1', pre_fork)], has_global_limit=True)
        assert plan['inloop'] == [_make_svc('mx1', pre_fork)]
        assert plan['forks'] == [] and plan['shared'] is False


def test_plan_single_service_prefork_main_is_worker_one():
    """One service with pre_fork 3 runs in main plus 2 forks on one port."""
    plan = _build_plan([_make_svc('mx1', 3)], has_global_limit=True)
    assert plan['inloop'][0]['name'] == 'mx1'  # main process serves
    assert plan['forks'] == [(_make_svc('mx1', 3), 2)]  # plus N-1 workers
    assert plan['reuse'] == {'mx1'}
    assert plan['shared'] is True


def test_plan_multi_services_rules():
    """Of several services only pre_fork 0 stays in main, all others fork."""
    services = [_make_svc('mx1', 5), _make_svc('mx2'), _make_svc('mx3', 1)]
    plan = _build_plan(services, has_global_limit=True)
    assert [svc['name'] for svc in plan['inloop']] == ['mx2']
    assert [(svc['name'], count) for svc, count in plan['forks']] == [('mx1', 5), ('mx3', 1)]
    assert plan['reuse'] == {'mx1'}  # only groups > 1 share a port
    assert plan['shared'] is True
    # all services forked: the master only supervises
    plan = _build_plan([_make_svc('mx1', 2), _make_svc('mx2', 1)], has_global_limit=False)
    assert plan['inloop'] == [] and plan['shared'] is False


@pytest.mark.parametrize('pre_fork', [-1, 1.5, '3', True], ids=['negative', 'float', 'str', 'bool'])
def test_plan_refuses_a_pre_fork_that_is_no_count(pre_fork):
    """A pre_fork that is no integer of 0 or more names the service, none starts."""
    with pytest.raises(TokeoSmtpdError, match=f"smtpd: service 'mx1': pre_fork must be an integer of 0 or more, not {pre_fork!r}"):
        _build_plan([_make_svc('mx1', pre_fork)], has_global_limit=False)


# ---- unit: shared limits ----------------------------------------------------


@isolated
def test_shared_limits_count_across_processes():
    """SharedGlobalLimits counts admits from a forked process."""
    limits = SharedGlobalLimits(max_processings=5, max_connections=7)
    assert isinstance(limits, GlobalLimits)
    assert limits.max_processings == 5 and limits.max_connections == 7

    def run_child(shared):
        shared.admit()
        shared.admit()
        shared.begin_processing()

    # the extension inherits the counters via os.fork; mirror that in the
    # test (the spawn default on macOS would also have to pickle 'run_child')
    proc = multiprocessing.get_context('fork').Process(target=run_child, args=(limits,))
    proc.start()
    proc.join(5)
    assert limits.active == 2 and limits.active_processings == 1
    limits.release()
    limits.end_processing()
    limits.release()
    limits.release()  # floor at 0
    assert limits.active == 0 and limits.active_processings == 0


@isolated
def test_shared_limits_give_the_last_slot_to_one_process_only():
    """Of forked processes taking the last slot at once exactly one gets it."""
    limits = SharedGlobalLimits(max_processings=1)
    context = multiprocessing.get_context('fork')
    start = context.Barrier(8)
    taken = context.Value('i', 0)

    def run_child():
        start.wait()
        if limits.try_begin_processing():
            with taken.get_lock():
                taken.value += 1

    procs = [context.Process(target=run_child) for _ in range(8)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(5)
    assert taken.value == 1 and limits.active_processings == 1


def test_shared_limits_slot_wait_recheck_interval():
    """slot_wait returns by its poll within 0.5 s without an event."""
    limits = SharedGlobalLimits(max_processings=1)

    async def go():
        limits.slot_free.clear()
        start = time.perf_counter()
        await limits.slot_wait()  # nobody sets the event
        return time.perf_counter() - start

    assert asyncio.run(go()) < 0.5  # returns via the poll


# ---- cement wiring ----------------------------------------------------------


def test_extension_loads_into_cement_app():
    """The extension loads its services and builds a server for each."""
    config = dict(
        smtpd=dict(
            services=[
                {
                    'name': 'mx-a',
                    'events_handler': 'tokeo.ext.smtpd:TokeoSmtpdEvents',
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
        (
            dict(events_handler='tokeo.ext.smtpd:TokeoSmtpdEvents', server_options=dict(encrypt_mode='TLS_NOPE')),
            'encrypt_mode must be one of',
        ),
        (dict(events_handler='NoDots'), "'NoDots' must be set as module:attr"),
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
        server = app.smtpd._build_server(dict(svc, name='mx-x', listeners=[], events_handler='tokeo.ext.smtpd:TokeoSmtpdEvents'), None)
        return app, server


def test_build_server_hands_the_options_to_the_handler():
    """The options of a service reach its events handler (ctx.options is gone)."""
    _, server = _build({'options': {'tag': 'opt-42'}})
    assert server.events_handler.options == {'tag': 'opt-42'}


def test_build_server_hands_the_server_options_to_the_server():
    """The server options of a service reach its server."""
    _, server = _build({'server_options': {'io_session_timeout': 60, 'crlf_data_mode': 'CRLF_DATA_STRICT', 'pre_fork': 2}})
    assert (server.io_session_timeout, server.crlf_data_mode.name) == (60, 'CRLF_DATA_STRICT')


def test_build_server_leaves_out_logger_severity():
    """A logger_severity of a service is left out, the server logs to app.log."""
    app, server = _build({'server_options': {'logger_severity': 'DEBUG'}})
    assert server.events_handler._logger_protected is app.log


def test_services_need_a_configured_service():
    """Without a configured service the services raise TokeoSmtpdError."""
    with SmtpdTest() as app:
        with pytest.raises(TokeoSmtpdError, match='no services configured'):
            app.smtpd.services()


def test_services_pick_the_named_ones_in_their_order():
    """Named services come back in the order of the names."""
    config = dict(smtpd=dict(services=[_make_service('mx-a', 2525), _make_service('mx-b', 2526)]))
    with SmtpdTest(config_defaults=config) as app:
        assert [svc['name'] for svc in app.smtpd.services(['mx-b', 'mx-a'])] == ['mx-b', 'mx-a']


@pytest.mark.parametrize(
    'argv, reason',
    [
        (['smtpd', 'serve'], 'give service names or --all$'),
        (['smtpd', 'serve', 'mx-a', '--all'], 'not both'),
    ],
    ids=['none', 'both'],
)
def test_serve_command_needs_names_or_all(argv, reason):
    """The serve command takes service names or --all, exactly one of both."""
    config = dict(smtpd=dict(services=[_make_service('mx-a', 2525)]))
    with SmtpdTest(argv=argv, config_defaults=config) as app:
        with pytest.raises(TokeoSmtpdError, match=reason):
            app.run()


def test_list_command_prints_the_services(capsys):
    """The list command prints the configured services."""
    config = dict(smtpd=dict(services=[_make_service('mx-a', 2525)]))
    with SmtpdTest(argv=['smtpd', 'list'], config_defaults=config) as app:
        app.run()
    assert 'mx-a' in capsys.readouterr().out


# ---- process tests: real termination behaviour ------------------------------


PROBE = 'tests.ext.smtpd.smtpd_tests_serve_probe'


def _make_service(name, port, pre_fork=0):
    svc = {
        'name': name,
        'events_handler': 'tokeo.ext.smtpd:TokeoSmtpdEvents',
        'listeners': [{'host': '127.0.0.1', 'port': port}],
    }
    if pre_fork:
        svc['server_options'] = {'pre_fork': pre_fork}
    return svc


def _serve(config, names=None):
    """The serve probe of the config, it leads a group with its workers."""
    return SubProcess.probe(PROBE, json.dumps(config), json.dumps(names), timeout=8)


def _has_banner(port):
    """Readiness: the SMTP banner answers on the port."""
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1) as sock:
            return sock.recv(4).startswith(b'220')
    except OSError:
        return False


def _get_child_pids(pid):
    """The forked workers of the master (Linux process tree)."""
    with open(f'/proc/{pid}/task/{pid}/children') as f:
        return [int(child) for child in f.read().split()]


def _check_ehlo(port):
    client = smtplib.SMTP('127.0.0.1', port, timeout=5)
    code, _ = client.ehlo('probe')
    client.quit()
    assert code == 250


def _assert_port_free(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))


def test_term_single_service_in_main_process():
    """SIGTERM ends one service in main with 0, the port is free again."""
    port = free_port()
    with _serve({'services': [_make_service('mx1', port)]}) as process:
        process.wait_for(lambda: _has_banner(port))
        _check_ehlo(port)
        process.signal(signal.SIGTERM)
        assert process.wait(8) == 0
        assert wait_process_group_ended(process.pid)
        _assert_port_free(port)


def test_term_single_service_with_global_limits():
    """A single service with global limits serves unforked and ends by SIGTERM."""
    port = free_port()
    with _serve({'max_processings': 2, 'services': [_make_service('mx1', port)]}) as process:
        process.wait_for(lambda: _has_banner(port))
        _check_ehlo(port)
        process.signal(signal.SIGTERM)
        assert process.wait(8) == 0
        assert wait_process_group_ended(process.pid)


def test_term_single_service_prefork_group():
    """SIGTERM ends main and its 2 forks with 0, the port is free again."""
    port = free_port()
    with _serve({'services': [_make_service('mx1', port, pre_fork=3)]}) as process:
        process.wait_for(lambda: _has_banner(port))
        if sys.platform.startswith('linux'):
            assert len(_get_child_pids(process.pid)) == 2
        _check_ehlo(port)
        process.signal(signal.SIGTERM)
        assert process.wait(8) == 0
        assert wait_process_group_ended(process.pid)
        _assert_port_free(port)


@needs_pdeathsig
def test_kill_master_takes_workers_down():
    """After SIGKILL of the master its workers end by the parent death signal."""
    port = free_port()
    with _serve({'services': [_make_service('mx1', port, pre_fork=3)]}) as process:
        process.wait_for(lambda: _has_banner(port))
        assert len(_get_child_pids(process.pid)) == 2
        process.signal(signal.SIGKILL)
        process.wait(8)
        assert wait_process_group_ended(process.pid)


def test_term_mixed_services_with_shared_limits():
    """One SIGTERM ends forked and main services with shared limits."""
    port_a, port_b = free_port(), free_port()
    config = {
        'max_connections': 10,
        'services': [_make_service('mx1', port_a, pre_fork=2), _make_service('mx2', port_b)],
    }
    with _serve(config) as process:
        process.wait_for(lambda: _has_banner(port_a))
        process.wait_for(lambda: _has_banner(port_b))
        _check_ehlo(port_a)
        _check_ehlo(port_b)
        process.signal(signal.SIGTERM)
        assert process.wait(8) == 0
        assert wait_process_group_ended(process.pid)
        _assert_port_free(port_a)
        _assert_port_free(port_b)


def _make_broken_service(name, pre_fork=0):
    """A service whose listener cannot bind (TEST-NET address)."""
    svc = {
        'name': name,
        'events_handler': 'tokeo.ext.smtpd:TokeoSmtpdEvents',
        'listeners': [{'host': '203.0.113.1', 'port': 2525}],
    }
    if pre_fork:
        svc['server_options'] = {'pre_fork': pre_fork}
    return svc


def test_failed_service_start_aborts_and_stops_started_ones():
    """A service that cannot bind ends the run with an error, ports free."""
    port_a = free_port()
    with _serve({'services': [_make_service('mx1', port_a), _make_broken_service('mx2')]}) as process:
        assert process.wait() != 0
        assert wait_process_group_ended(process.pid)
        _assert_port_free(port_a)


def test_failed_start_terminates_forked_workers():
    """A failed start in main also ends the forked workers, before their SIGKILL."""
    port_a = free_port()
    started = time.monotonic()
    with _serve({'services': [_make_service('mx1', port_a, pre_fork=2), _make_broken_service('mx2')]}) as process:
        assert process.wait() != 0
        # the workers stop on the SIGTERM of the master; a lost one costs the
        # 5 s until the SIGKILL of the bounded wait
        assert time.monotonic() - started < 4.0
        assert wait_process_group_ended(process.pid)
        _assert_port_free(port_a)


def test_broken_config_of_a_worker_stops_before_the_fork():
    """A worker service with a broken config ends the start before any fork."""
    port_a, port_b = free_port(), free_port()
    broken = _make_service('mx2', port_b, pre_fork=1)
    broken['server_options']['encrypt_mode'] = 'TLS_NOPE'
    with _serve({'services': [_make_service('mx1', port_a), broken]}) as process:
        assert process.wait() != 0
        assert wait_process_group_ended(process.pid)
        log = process.output
        assert "smtpd: service 'mx2': encrypt_mode must be one of" in log
        assert 'forked workers' not in log
        _assert_port_free(port_a)


def test_failed_worker_stops_all_services():
    """A worker that cannot bind logs why and ends all services, ports free."""
    port_a = free_port()
    with _serve({'services': [_make_service('mx1', port_a), _make_broken_service('mx2', pre_fork=1)]}) as process:
        assert process.wait() != 0
        assert wait_process_group_ended(process.pid)
        log = process.output
        assert "of service 'mx2' failed: could not bind" in log
        assert "of service 'mx2' ended with 1, all services stopped" in log
        _assert_port_free(port_a)


# ---- process tests: services in the forked workers --------------------------


def _make_tagged_service(name, port, pre_fork, server_options=None):
    """A service whose handler answers EHLO with its tag and pid."""
    svc = _make_service(name, port, pre_fork=pre_fork)
    svc['events_handler'] = 'tests.ext.smtpd.smtpd_tests_helper:TaggedSmtpdEvents'
    svc['options'] = {'tag': 'opt-42'}
    svc['server_options'].update(server_options or {})
    return svc


def _open_dialogs(port, count):
    """Open count connections and read their banner."""
    socks = []
    for _ in range(count):
        sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        assert sock.recv(1024).startswith(b'220')
        socks.append(sock)
    return socks


def test_prefork_workers_hand_the_options_to_their_handler():
    """Every process of a group of 3 answers EHLO with the tag of the options."""
    port = free_port()
    with _serve({'services': [_make_tagged_service('mx1', port, pre_fork=3)]}) as process:
        process.wait_for(lambda: _has_banner(port))
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
            assert pids <= {process.pid, *_get_child_pids(process.pid)}


def test_prefork_workers_apply_the_server_options():
    """Open dialogs of a group of 3 end by 421 after io_session_timeout of 1 s."""
    port = free_port()
    with _serve({'services': [_make_tagged_service('mx1', port, pre_fork=3, server_options={'io_session_timeout': 1})]}) as process:
        process.wait_for(lambda: _has_banner(port))
        socks = _open_dialogs(port, 6)
        for sock in socks:
            assert sock.recv(1024).startswith(b'421')
            sock.close()


def test_term_prefork_group_ends_open_dialogs_with_421():
    """SIGTERM ends the waiting dialogs of all processes of a group of 3 by 421."""
    port = free_port()
    # SO_REUSEPORT may hand all 6 dialogs to one process, it needs a slot for each
    service = _make_tagged_service('mx1', port, pre_fork=3, server_options={'max_processings': 6})
    with _serve({'services': [service]}) as process:
        process.wait_for(lambda: _has_banner(port))
        socks = _open_dialogs(port, 6)
        process.signal(signal.SIGTERM)
        for sock in socks:
            assert sock.recv(1024).startswith(b'421')
            sock.close()
        assert process.wait(8) == 0
        assert wait_process_group_ended(process.pid)


@needs_pdeathsig
def test_prefork_logs_the_started_service_of_every_process():
    """The master logs its workers, every process its started service and pid."""
    port = free_port()
    with _serve({'services': [_make_tagged_service('mx1', port, pre_fork=3)]}) as process:
        process.wait_for(lambda: _has_banner(port))
        pids = [process.pid, *_get_child_pids(process.pid)]
        assert len(pids) == 3
        started = [f"smtpd: service 'mx1' started (pid {pid})" for pid in pids]
        assert wait_until(lambda: all(line in process.output for line in started))
        forked = re.search(r'smtpd: forked workers \[([0-9, ]+)\]', process.output)
        assert {int(pid) for pid in forked.group(1).split(',')} == set(pids[1:])


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


def test_prefork_workers_present_the_configured_certificate(cert_files):
    """All processes of a group of 3 present the certificate of tls_cert_path."""
    cert_path, key_path, fingerprint = cert_files
    port = free_port()
    server_options = {'encrypt_mode': 'TLS_OPTIONAL', 'tls_cert_path': cert_path, 'tls_key_path': key_path}
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with _serve({'services': [_make_tagged_service('mx1', port, pre_fork=3, server_options=server_options)]}) as process:
        process.wait_for(lambda: _has_banner(port))
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
