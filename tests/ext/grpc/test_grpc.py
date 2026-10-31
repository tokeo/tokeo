"""
Tests for tokeo.ext.grpc: the config read at setup, the lazy server with its
start, stop and serve on a free localhost port, the interceptor chain, the
grpc serve command and TLS with the auto certificate, a custom certificate
and the client ca for mutual TLS.

Every server runs in process on localhost and is stopped after its test. The
echo service of the helper is built from grpc generic handlers, so no .proto
file is compiled and no generated code is needed.
"""

import socket
import signal
import contextlib
import ipaddress
from types import SimpleNamespace
from concurrent import futures
from datetime import datetime, timedelta, timezone

import grpc
import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cement.core.exc import CaughtSignal
from tokeo.ext.grpc import TokeoGrpcError
from tests.ext.grpc.grpc_tests_helper import GrpcTest, SERVICE, ADD_SERVICER, SERVICER, INTERCEPTORS


def _free_port():
    # a port on localhost that is free right now; the extension drops the port
    # grpc binds for port 0, so the tests need a fixed one
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('localhost', 0))
        return sock.getsockname()[1]


def _stop(app):
    # stop the server of the app, if it was built, and wait until it stopped
    if app.grpc._server is not None:
        app.grpc._server.stop(0).wait(5)


def _call(port, method='Say', request=b'hi', credentials=None, metadata=None):
    # one call to the echo service on localhost:port, plain or by credentials
    target = f'localhost:{port}'
    channel = grpc.secure_channel(target, credentials) if credentials else grpc.insecure_channel(target)
    with channel:
        return channel.unary_unary(f'/{SERVICE}/{method}')(request, timeout=5, metadata=metadata)


def _refused(port, **kw):
    # the status code of a call to the echo service that fails
    with pytest.raises(grpc.RpcError) as err:
        _call(port, **kw)
    return err.value.code()


def _key():
    # a fast ec key for the certificates of the tests
    return ec.generate_private_key(ec.SECP256R1())


def _cert(name, key, issuer=None, issuer_key=None, ca=False):
    # a certificate for name with name as dns san, self signed without issuer
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer.subject if issuer else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
    )
    return builder.sign(issuer_key or key, hashes.SHA256())


def _pem(obj):
    # the pem bytes of a certificate or a private key
    if isinstance(obj, x509.Certificate):
        return obj.public_bytes(serialization.Encoding.PEM)
    return obj.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _write(directory, name, data):
    # write data as file name into directory and return its path as str
    path = directory / name
    path.write_bytes(data)
    return str(path)


@pytest.fixture(scope='module')
def certs(tmp_path_factory):
    """A ca with server and client cert, a client cert of a foreign ca."""
    tmp = tmp_path_factory.mktemp('certs')
    ca_key, foreign_key, server_key, client_key, stranger_key = _key(), _key(), _key(), _key(), _key()
    ca = _cert('tokeo test ca', ca_key, ca=True)
    foreign = _cert('foreign test ca', foreign_key, ca=True)
    server = _cert('localhost', server_key, ca, ca_key)
    client = _cert('client.tokeo.test', client_key, ca, ca_key)
    stranger = _cert('stranger.tokeo.test', stranger_key, foreign, foreign_key)
    return SimpleNamespace(
        ca=_pem(ca),
        ca_file=_write(tmp, 'ca.pem', _pem(ca)),
        server_cert_file=_write(tmp, 'server.pem', _pem(server)),
        server_key_file=_write(tmp, 'server.key', _pem(server_key)),
        client=(_pem(client_key), _pem(client)),
        stranger=(_pem(stranger_key), _pem(stranger)),
    )


@pytest.fixture
def start_grpc():
    """Starts test apps on a free localhost port, stops and closes them after."""
    with contextlib.ExitStack() as stack:

        def start(settings=None, startup=True):
            port = _free_port()
            app = stack.enter_context(GrpcTest(settings={'url': f'localhost:{port}', **(settings or {})}))
            # the stack runs this before it closes the app
            stack.callback(_stop, app)
            if startup:
                app.grpc.startup()
            return app, port

        yield start


# _setup


def test_setup_merges_the_defaults_below_the_settings():
    """The defaults fill the grpc section, a given url and max_worker stay."""
    with GrpcTest(settings={'url': 'localhost:1234', 'max_worker': 3}) as app:
        assert app.config.get('grpc', 'url') == 'localhost:1234'
        assert app.config.get('grpc', 'max_worker') == 3
        assert app.config.get('grpc', 'tls_enabled') is False
        assert app.config.get('grpc', 'tls_key_type') == 'ec'
        assert app.config.get('grpc', 'tls_key_size') == 521
        assert app.config.get('grpc', 'tls_valid_days') == 90


def test_setup_splits_module_and_attribute_of_each_setting():
    """Each module:attribute setting is split into module and attribute."""
    with GrpcTest(settings={'grpc_interceptor': INTERCEPTORS}) as app:
        ext = app.grpc
        assert [ext._proto_add_servicer_to_server_module, ext._proto_add_servicer_to_server_method] == ADD_SERVICER.split(':')
        assert [ext._grpc_servicer_module, ext._grpc_servicer_method] == SERVICER.split(':')
        assert [ext._grpc_interceptor_module, ext._grpc_interceptor_method] == INTERCEPTORS.split(':')


def test_setup_without_interceptor_leaves_it_unset():
    """Without grpc_interceptor no interceptor module is set."""
    with GrpcTest() as app:
        assert app.grpc._grpc_interceptor_module == ''


@pytest.mark.parametrize('key', ['proto_add_servicer_to_server', 'grpc_servicer'])
def test_setup_mandatory_setting_missing_raises(key):
    """An unset mandatory setting stops the setup with TokeoGrpcError."""
    with pytest.raises(TokeoGrpcError, match=f'Missing mandatory {key} setting in config'):
        with GrpcTest(settings={key: None}):
            pass


@pytest.mark.xfail(strict=True, raises=IndexError, reason='a setting without a colon raises IndexError (grpc.py:143, 149, 155)')
@pytest.mark.parametrize('key', ['proto_add_servicer_to_server', 'grpc_servicer', 'grpc_interceptor'])
def test_setup_setting_without_colon_raises(key):
    """A module path without :attribute stops the setup with TokeoGrpcError."""
    with pytest.raises(TokeoGrpcError):
        with GrpcTest(settings={key: 'tests.ext.grpc.grpc_tests_helper'}):
            pass


def test_setup_refuses_a_different_app():
    """_setup with an app other than its own raises TokeoGrpcError."""
    with GrpcTest() as app:
        with pytest.raises(TokeoGrpcError, match='received a different app'):
            app.grpc._setup(object())


# server


def test_server_is_built_lazily_and_once(start_grpc):
    """The setup builds no server, the property builds one and keeps it."""
    app, _ = start_grpc(startup=False)
    assert app.grpc._server is None
    server = app.grpc.server
    assert server is not None
    assert app.grpc.server is server


def test_server_uses_max_worker_threads(start_grpc, monkeypatch):
    """The thread pool of the server gets max_worker 3 as max_workers."""
    pools = []

    def pool(max_workers):
        # records max_workers and hands out a real pool
        pools.append(max_workers)
        return futures.ThreadPoolExecutor(max_workers=max_workers)

    # only the name futures of the extension is replaced, not the module
    monkeypatch.setattr('tokeo.ext.grpc.futures', SimpleNamespace(ThreadPoolExecutor=pool))
    _, port = start_grpc({'max_worker': 3})
    assert pools == [3]
    assert _call(port) == b'echo hi'


def test_server_without_tls_builds_no_credentials(start_grpc, monkeypatch):
    """With tls_enabled false no tls credentials are built, calls are served."""
    app, port = start_grpc(startup=False)
    monkeypatch.setattr(app.grpc, '_tls_server_credentials', lambda: pytest.fail('tls credentials built'))
    app.grpc.startup()
    assert _call(port) == b'echo hi'


# startup and shutdown


def test_startup_serves_the_echo_service(start_grpc):
    """After startup a call of Say on localhost answers echo hi."""
    _, port = start_grpc()
    assert _call(port, request=b'hi') == b'echo hi'


def test_shutdown_stops_the_server(start_grpc):
    """After shutdown the port refuses a call with UNAVAILABLE."""
    app, port = start_grpc()
    assert _call(port) == b'echo hi'
    app.grpc.shutdown()
    app.grpc.server.wait_for_termination(5)
    assert _refused(port) == grpc.StatusCode.UNAVAILABLE


# interceptors


def test_interceptors_of_grpc_interceptor_guard_the_calls(start_grpc):
    """The interceptors refuse a call without x-token and pass one with it."""
    _, port = start_grpc({'grpc_interceptor': INTERCEPTORS})
    assert _refused(port) == grpc.StatusCode.UNAUTHENTICATED
    assert _call(port, metadata=[('x-token', 'secret')]) == b'echo hi'


# serve


@pytest.mark.parametrize('interrupt', [KeyboardInterrupt(), CaughtSignal(signal.SIGTERM, None)], ids=['keyboard', 'signal'])
def test_serve_serves_until_interrupted_then_stops(start_grpc, monkeypatch, interrupt):
    """serve logs the url, serves and stops on KeyboardInterrupt or CaughtSignal."""
    app, port = start_grpc(startup=False)
    logged = []
    monkeypatch.setattr(app.log, 'info', lambda msg, **kw: logged.append(msg))
    answers = []

    def wait_for_termination(timeout=None):
        # stands in for the blocking wait: one call, then the interruption
        answers.append(_call(port))
        raise interrupt

    monkeypatch.setattr(app.grpc.server, 'wait_for_termination', wait_for_termination)
    app.grpc.serve()
    assert answers == [b'echo hi']
    assert logged == [f'Grpc server started, listening on localhost:{port}']
    assert _refused(port) == grpc.StatusCode.UNAVAILABLE


def test_controller_grpc_serve_runs_serve(monkeypatch):
    """The command grpc serve calls serve of the extension."""
    served = []
    with GrpcTest(argv=['grpc', 'serve']) as app:
        monkeypatch.setattr(app.grpc, 'serve', lambda: served.append(True))
        app.run()
    assert served == [True]


# _generate_self_signed_cert


def _auto_cert(settings=None):
    # the key and the parsed certificate the auto cert path makes for settings
    with GrpcTest(settings={'url': 'localhost:50051', **(settings or {})}) as app:
        key_pem, cert_pem = app.grpc._generate_self_signed_cert()
    return serialization.load_pem_private_key(key_pem, password=None), x509.load_pem_x509_certificate(cert_pem)


def _sans_of(cert):
    # the subject alternative names of a certificate
    return list(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value)


def test_generate_self_signed_cert_defaults():
    """By default a self signed localhost cert, ec P-521 key, valid 90 days."""
    key, cert = _auto_cert()
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    assert key.curve.name == 'secp521r1'
    assert cert.subject == cert.issuer
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == 'localhost'
    assert _sans_of(cert) == [x509.DNSName('localhost')]
    assert cert.not_valid_after_utc - cert.not_valid_before_utc == timedelta(days=90)
    cert.verify_directly_issued_by(cert)


@pytest.mark.parametrize('size, curve', [(256, 'secp256r1'), (384, 'secp384r1'), ('521', 'secp521r1')])
def test_generate_self_signed_cert_ec_key_size_picks_the_curve(size, curve):
    """tls_key_size 256, 384 and 521 pick the curves P-256, P-384 and P-521."""
    key, _ = _auto_cert({'tls_key_type': 'EC', 'tls_key_size': size})
    assert key.curve.name == curve


def test_generate_self_signed_cert_rsa_key():
    """tls_key_type rsa with tls_key_size 2048 makes an rsa key of 2048 bits."""
    key, _ = _auto_cert({'tls_key_type': 'rsa', 'tls_key_size': 2048})
    assert isinstance(key, rsa.RSAPrivateKey)
    assert key.key_size == 2048


def test_generate_self_signed_cert_tls_cn_sets_only_the_subject():
    """tls_cn becomes the common name, the san stays the url host."""
    _, cert = _auto_cert({'tls_cn': 'Tokeo Test'})
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == 'Tokeo Test'
    assert _sans_of(cert) == [x509.DNSName('localhost')]


def test_generate_self_signed_cert_tls_sans_typed_and_without_duplicates():
    """tls_sans follow the url host once each, typed as ip address or dns."""
    _, cert = _auto_cert({'tls_sans': ['localhost', '127.0.0.1', 'api.tokeo.test', '::1', '127.0.0.1', '']})
    assert _sans_of(cert) == [
        x509.DNSName('localhost'),
        x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
        x509.DNSName('api.tokeo.test'),
        x509.IPAddress(ipaddress.ip_address('::1')),
    ]


def test_generate_self_signed_cert_ipv6_url_host():
    """The url [::1]:50051 gives the common name ::1 and the ip san ::1."""
    _, cert = _auto_cert({'url': '[::1]:50051'})
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == '::1'
    assert _sans_of(cert) == [x509.IPAddress(ipaddress.ip_address('::1'))]


@pytest.mark.parametrize('days', [7, '7'], ids=['int', 'str'])
def test_generate_self_signed_cert_tls_valid_days(days):
    """tls_valid_days 7, also given as str, makes the cert valid for 7 days."""
    _, cert = _auto_cert({'tls_valid_days': days})
    assert cert.not_valid_after_utc - cert.not_valid_before_utc == timedelta(days=7)


@pytest.mark.parametrize(
    'settings, message',
    [
        ({'tls_key_type': 'dsa'}, "tls_key_type 'dsa' is not supported"),
        ({'tls_key_type': 'rsa', 'tls_key_size': 1024}, 'tls_key_size 1024 is not valid for rsa'),
        ({'tls_key_type': 'ec', 'tls_key_size': 512}, 'tls_key_size 512 is not valid for ec'),
        ({'tls_key_size': 'big'}, 'tls_key_size is not a valid number'),
        ({'tls_valid_days': 'soon'}, 'tls_valid_days is not a valid number'),
    ],
    ids=['key_type', 'rsa_size', 'ec_size', 'key_size_nan', 'valid_days_nan'],
)
def test_generate_self_signed_cert_invalid_settings_raise(settings, message):
    """A bad key type, key size or number of days raises TokeoGrpcError."""
    with pytest.raises(TokeoGrpcError, match=message):
        _auto_cert(settings)


# tls with the auto certificate


def _record_auto_cert(app, monkeypatch):
    # keep the (key_pem, cert_pem) pairs the auto cert path of the app makes
    made = []
    generate = app.grpc._generate_self_signed_cert

    def record():
        made.append(generate())
        return made[-1]

    monkeypatch.setattr(app.grpc, '_generate_self_signed_cert', record)
    return made


def test_tls_auto_cert_serves_clients_trusting_it(start_grpc, monkeypatch):
    """With tls but no cert files a client trusting the auto cert is served."""
    app, port = start_grpc({'tls_enabled': True, 'tls_key_size': 256}, startup=False)
    made = _record_auto_cert(app, monkeypatch)
    app.grpc.startup()
    assert len(made) == 1
    credentials = grpc.ssl_channel_credentials(root_certificates=made[0][1])
    assert _call(port, credentials=credentials) == b'echo hi'
    assert _refused(port) == grpc.StatusCode.UNAVAILABLE


def test_tls_auto_cert_is_not_trusted_by_others(start_grpc, certs):
    """A client trusting only another ca refuses the auto cert, UNAVAILABLE."""
    _, port = start_grpc({'tls_enabled': True, 'tls_key_size': 256})
    assert _refused(port, credentials=grpc.ssl_channel_credentials(root_certificates=certs.ca)) == grpc.StatusCode.UNAVAILABLE


# tls with a custom certificate


def test_tls_custom_cert_is_served(start_grpc, certs, monkeypatch):
    """tls_certificate and tls_key are served instead of an auto cert."""
    app, port = start_grpc(
        {'tls_enabled': True, 'tls_certificate': certs.server_cert_file, 'tls_key': certs.server_key_file}, startup=False
    )
    monkeypatch.setattr(app.grpc, '_generate_self_signed_cert', lambda: pytest.fail('auto cert generated'))
    app.grpc.startup()
    assert _call(port, credentials=grpc.ssl_channel_credentials(root_certificates=certs.ca)) == b'echo hi'


@pytest.mark.parametrize('given', ['tls_certificate', 'tls_key'])
def test_tls_custom_cert_needs_cert_and_key(start_grpc, certs, given):
    """Only one of tls_certificate and tls_key raises TokeoGrpcError on startup."""
    files = {'tls_certificate': certs.server_cert_file, 'tls_key': certs.server_key_file}
    app, _ = start_grpc({'tls_enabled': True, given: files[given]}, startup=False)
    with pytest.raises(TokeoGrpcError, match='tls_certificate and tls_key must both be set'):
        app.grpc.startup()


@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason='a failed tls setup keeps a half built server without port and service (grpc.py:230-241)',
)
def test_tls_failed_setup_fails_again_on_the_next_startup(start_grpc, certs):
    """After a failed tls setup a second startup raises again."""
    app, _ = start_grpc({'tls_enabled': True, 'tls_certificate': certs.server_cert_file}, startup=False)
    with pytest.raises(TokeoGrpcError):
        app.grpc.startup()
    with pytest.raises(TokeoGrpcError):
        app.grpc.startup()


# tls with a client ca (mutual tls)


def _custom_tls(certs, **settings):
    # the settings for tls by the custom server cert of certs plus settings
    return {'tls_enabled': True, 'tls_certificate': certs.server_cert_file, 'tls_key': certs.server_key_file, **settings}


def _client(certs, pair=None):
    # channel credentials that trust the ca of certs, with a client cert pair
    key, chain = pair or (None, None)
    return grpc.ssl_channel_credentials(root_certificates=certs.ca, private_key=key, certificate_chain=chain)


def test_tls_client_ca_hands_the_client_cert_on(start_grpc, certs):
    """With tls_client_ca the service sees the name of the client cert."""
    _, port = start_grpc(_custom_tls(certs, tls_client_ca=certs.ca_file))
    assert _call(port, method='Peer', credentials=_client(certs, certs.client)) == b'client.tokeo.test'


def test_tls_client_ca_requires_a_client_cert(start_grpc, certs):
    """With tls_client_ca a client without cert is refused, one of the ca served."""
    _, port = start_grpc(_custom_tls(certs, tls_client_ca=certs.ca_file))
    assert _refused(port, credentials=_client(certs)) == grpc.StatusCode.UNAVAILABLE
    assert _call(port, credentials=_client(certs, certs.client)) == b'echo hi'


def test_tls_client_ca_refuses_a_cert_of_another_ca(start_grpc, certs):
    """A client cert of a foreign ca is refused with UNAVAILABLE."""
    _, port = start_grpc(_custom_tls(certs, tls_client_ca=certs.ca_file))
    assert _refused(port, credentials=_client(certs, certs.stranger)) == grpc.StatusCode.UNAVAILABLE


def test_tls_client_ca_with_the_auto_cert(start_grpc, certs, monkeypatch):
    """A client ca works with the auto cert as server cert too."""
    app, port = start_grpc({'tls_enabled': True, 'tls_key_size': 256, 'tls_client_ca': certs.ca_file}, startup=False)
    made = _record_auto_cert(app, monkeypatch)
    app.grpc.startup()
    key, chain = certs.client
    credentials = grpc.ssl_channel_credentials(root_certificates=made[0][1], private_key=key, certificate_chain=chain)
    assert _call(port, method='Peer', credentials=credentials) == b'client.tokeo.test'
    assert _refused(port, credentials=grpc.ssl_channel_credentials(root_certificates=made[0][1])) == grpc.StatusCode.UNAVAILABLE
