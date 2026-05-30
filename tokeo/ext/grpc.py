"""
Tokeo gRPC Extension Module.

This module provides gRPC integration for Tokeo applications, allowing Tokeo apps
to serve as gRPC servers and clients. It includes a controller for managing gRPC
services and templates for generating client/server implementations.

The extension handles server lifecycle management, dynamic loading of servicers,
and configuration management.

### Features

- Dynamically loads servicers and protocol buffer implementations from the
    configured module and class paths
- Manages the server lifecycle with clean startup and shutdown
- Adds CLI commands to run and control the gRPC server
- Logs through the application's logging system
- Reads workers, TLS, and interceptor settings from the extension config
- Generates gRPC scaffolding for new Tokeo projects

"""

from sys import argv
from os.path import basename
from tokeo.ext.argparse import Controller
from tokeo.core.exc import TokeoError
from cement.core.meta import MetaMixin
from cement.core.exc import CaughtSignal
from cement import ex
from concurrent import futures
import grpc
import importlib


class TokeoGrpcError(TokeoError):
    """
    Exception class for gRPC extension errors.

    This class is used to raise and catch exceptions that are specific to
    the Tokeo gRPC extension functionality.

    ### Notes

    : Inherits from TokeoError to maintain consistent error handling

    : Raised on configuration or wiring issues of the gRPC extension

    """

    pass


class TokeoGrpc(MetaMixin):
    """
    The TokeoGrpc extension integrates gRPC server within Tokeo applications.

    This class manages the gRPC server lifecycle and configuration.
    It dynamically loads the gRPC servicer and protocol buffer implementations
    based on configuration, providing a seamless integration between
    Tokeo applications and gRPC services.

    ### Notes

    - The server is lazily initialized when first accessed through
        the ``server`` property
    - Server configuration is drawn from the application's 'grpc' config section
    - Provides methods for startup, shutdown, and serving (blocking mode)
    - Supports dynamic loading of servicer implementations
    """

    class Meta:
        """Extension meta-data and configuration defaults."""

        #: Unique identifier for this handler
        label = 'tokeo.grpc'

        #: Id for config
        config_section = 'grpc'

        #: Dict with initial settings
        config_defaults = dict(
            # address and port to run the service on
            url='localhost:50051',
            # number of concurrent workers
            max_worker=1,
            # modules and methods for server, interceptor and service
            proto_add_servicer_to_server=None,
            grpc_servicer=None,
            grpc_interceptor=None,
            # tls is opt-in; while disabled no tls code or library is touched
            tls_enabled=False,
            # custom cert: paths to pem files for cert and key (both or none)
            tls_certificate=None,
            tls_key=None,
            # validity for the in-memory auto cert when no custom cert is set
            tls_valid_days=90,
            # auto cert subject common name (cosmetic); None -> from url host
            tls_cn=None,
            # auto cert extra names/ips for SAN (url host is always included)
            tls_sans=None,
            # auto cert key: 'rsa' (size in bits) or 'ec' (size = curve 256/384/521)
            tls_key_type='ec',
            tls_key_size=521,
            # client ca to allow client validation by client certificates
            tls_client_ca=None,
            # require a valid client cert, else forward validation to interceptors
            tls_require_client_cert=False,
        )

    def __init__(self, app, *args, **kw):
        super(TokeoGrpc, self).__init__(*args, **kw)
        self.app = app
        self._server = None
        self._proto_add_servicer_to_server_module = ''
        self._proto_add_servicer_to_server_method = ''
        self._grpc_servicer_module = ''
        self._grpc_servicer_method = ''
        self._grpc_interceptor_module = ''
        self._grpc_interceptor_method = ''

    def _setup(self, app):
        # the app handed to _setup must be the same instance this handler
        # was initialized with; guard against accidental mis-wiring
        if app is not self.app:
            raise TokeoGrpcError('_setup() received a different app than the one TokeoGrpc was initialized with')
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        a = self._config('proto_add_servicer_to_server')
        if not a:
            raise TokeoGrpcError('Missing mandatory proto_add_servicer_to_server setting in config')
        a = a.split(':')
        self._proto_add_servicer_to_server_module = a[0]
        self._proto_add_servicer_to_server_method = a[1]
        a = self._config('grpc_servicer')
        if not a:
            raise TokeoGrpcError('Missing mandatory grpc_servicer setting in config')
        a = a.split(':')
        self._grpc_servicer_module = a[0]
        self._grpc_servicer_method = a[1]
        # optional interceptor chain factory (module:method); skip when unset
        a = self._config('grpc_interceptor')
        if a:
            a = a.split(':')
            self._grpc_interceptor_module = a[0]
            self._grpc_interceptor_method = a[1]

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a convenient wrapper around the application's config.get method,
        accessing values from the extension's config section.

        ### Args

        - **key** (str): Configuration key to retrieve
        - **kwargs**: Additional arguments passed to config.get()

        ### Returns

        - **Any**: The configuration value for the specified key

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    @property
    def server(self):
        """
        Get the gRPC server instance, creating it if needed.

        This property lazily initializes the gRPC server when first accessed.
        The initialization dynamically imports the required modules and registers
        the configured servicer with the gRPC server.

        ### Returns

        - **grpc.Server**: The configured gRPC server instance

        ### Notes

        : The initialization process follows these steps:

            - Creates a server with ThreadPoolExecutor using configured
                max_worker count
            - Dynamically imports the protocol buffer module containing the
                add_servicer_to_server function
            - Dynamically imports the servicer class module
            - Resolves the optional interceptor chain from grpc_interceptor
                and passes it to the server at construction
            - Registers the servicer with the server using the
                add_servicer_to_server function
            - Binds the listen url, either as a TLS secured port when
                tls_enabled is set or as a plain insecure port otherwise

        : TLS is fully opt-in. With tls_enabled false the server never imports
            or calls any TLS code. With it true, a custom tls_certificate /
            tls_key pair is used when both are given, otherwise an in-memory
            self signed cert (valid for tls_valid_days) is generated and never
            written to disk

        """
        if self._server is None:
            # resolve the optional interceptor chain before the server is
            # built, interceptors can only be passed at construction time
            if self._grpc_interceptor_module:
                grpc_interceptor_module = importlib.import_module(self._grpc_interceptor_module)
                grpc_interceptor_method = getattr(grpc_interceptor_module, self._grpc_interceptor_method)
                interceptors = grpc_interceptor_method()
            else:
                interceptors = None
            # get dynamic methods for proto and service
            proto_add_servicer_to_server_module = importlib.import_module(self._proto_add_servicer_to_server_module)
            proto_add_servicer_to_server_method = getattr(proto_add_servicer_to_server_module, self._proto_add_servicer_to_server_method)
            grpc_servicer_module = importlib.import_module(self._grpc_servicer_module)
            grpc_servicer_method = getattr(grpc_servicer_module, self._grpc_servicer_method)
            # create the server
            self._server = grpc.server(
                futures.ThreadPoolExecutor(max_workers=self._config('max_worker')),
                interceptors=interceptors,
            )
            # bind the listen port; tls is opt-in and only that branch pulls
            # in any tls machinery, so a plain server never touches tls code
            if self._config('tls_enabled'):
                self._server.add_secure_port(self._config('url'), self._tls_server_credentials())
            else:
                self._server.add_insecure_port(self._config('url'))
            # append services
            proto_add_servicer_to_server_method(grpc_servicer_method(), self._server)

        return self._server

    def _tls_server_credentials(self):
        """
        Build the gRPC server credentials for a TLS secured port.

        Two modes are selected by configuration: a custom certificate when
        both tls_certificate and tls_key point to PEM files, otherwise an
        auto generated in-memory self signed certificate. When tls_client_ca
        is set, mutual TLS is enabled on top and clients may get verified
        against that ca.

        ### Returns

        - **grpc.ServerCredentials**: Credentials to pass to add_secure_port

        ### Raises

        - **TokeoGrpcError**: If only one of tls_certificate / tls_key is set

        ### Notes

        : The custom cert files (and the client ca, if any) are the only
            things read from disk; the auto cert path keeps everything in
            memory

        : mTLS is opt-in via tls_client_ca; tls_require_client_cert decides
            whether a client cert is mandatory, otherwise validation is left
            to the interceptors

        """
        cert = self._config('tls_certificate')
        key = self._config('tls_key')
        if cert and key:
            # custom cert: read the configured pem files as given
            with open(key, 'rb') as f:
                key_pem = f.read()
            with open(cert, 'rb') as f:
                cert_pem = f.read()
        elif cert or key:
            raise TokeoGrpcError('tls_certificate and tls_key must both be set to use a custom certificate')
        else:
            # auto cert: generate a self signed certificate in memory
            key_pem, cert_pem = self._generate_self_signed_cert()
        # optional mtls: when a client ca is set, verify client certs too
        ca = self._config('tls_client_ca')
        if ca:
            with open(ca, 'rb') as f:
                ca_pem = f.read()
            return grpc.ssl_server_credentials(
                [(key_pem, cert_pem)],
                root_certificates=ca_pem,
                require_client_auth=bool(self._config('tls_require_client_cert')),
            )
        return grpc.ssl_server_credentials([(key_pem, cert_pem)])

    def _generate_self_signed_cert(self):
        """
        Create an in-memory self signed certificate and matching key.

        The certificate is valid for tls_valid_days days and is never written
        to disk; both PEM blobs only live in memory for the server lifetime.
        Key, subject and SANs are driven by the tls_* auto cert config.

        ### Returns

        - **tuple**: A (key_pem, cert_pem) pair of PEM encoded bytes

        ### Raises

        - **TokeoGrpcError**: On an unsupported tls_key_type or an ec
            tls_key_size that is not one of 256, 384, 521

        ### Notes

        : The cryptography package is imported lazily here, so it stays out
            of the non-tls and custom-cert code paths entirely

        : The host from the url plus every tls_sans entry become SANs, each
            added as an ip address when it parses as one, otherwise as a dns
            name; client validation uses these SANs, not the CN

        : tls_cn only sets the (cosmetic) subject common name; when unset the
            url host is used

        """
        # lazy import: cryptography is only needed for the auto cert path
        import ipaddress
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa, ec

        # derive the host from a "host:port" url (strip ipv6 brackets); the
        # host is both the cn fallback and the first san
        host = self._config('url').rsplit(':', 1)[0].strip('[]') or 'localhost'
        # private key: rsa by bit size, or ec by curve (256, 384, 521)
        key_type = (self._config('tls_key_type') or 'ec').lower()
        # get key_size as int
        try:
            key_size = int(str(self._config('tls_key_size') or 521))
        except ValueError:
            raise TokeoGrpcError('tls_key_size is not a valid number')
        # generate key but validate also senseful key_size
        if key_type == 'rsa':
            if key_size < 2048:
                raise TokeoGrpcError(f'tls_key_size {key_size} is not valid for rsa (use 2048 or greater)')
            key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
        elif key_type == 'ec':
            curves = {256: ec.SECP256R1, 384: ec.SECP384R1, 521: ec.SECP521R1}
            if key_size not in curves:
                raise TokeoGrpcError(f'tls_key_size {key_size} is not valid for ec (use 256, 384 or 521)')
            key = ec.generate_private_key(curves[key_size]())
        else:
            raise TokeoGrpcError(f"tls_key_type '{key_type}' is not supported (use 'rsa' or 'ec')")

        # subject/issuer cn: tls_cn override or the url host
        cn = self._config('tls_cn') or host
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

        # build sans from host + tls_sans, dedup, typed as ip or dns name
        sans = []
        seen = set()
        for entry in [host, *(self._config('tls_sans') or [])]:
            if not entry or entry in seen:
                continue
            seen.add(entry)
            # using the builtin validation and exception to check wether an entry
            # is an ip address or DNS name
            try:
                sans.append(x509.IPAddress(ipaddress.ip_address(entry)))
            except ValueError:
                sans.append(x509.DNSName(entry))

        # parse validity as int, mirroring the tls_key_size handling above
        try:
            valid_days = int(str(self._config('tls_valid_days') or 90))
        except ValueError:
            raise TokeoGrpcError('tls_valid_days is not a valid number')

        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=valid_days))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .sign(key, hashes.SHA256())
        )
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        return key_pem, cert_pem

    def startup(self):
        """
        Start the gRPC server.

        This method initiates the server but does not block. The server
        begins accepting connections after this method is called.

        """
        self.server.start()

    def shutdown(self):
        """
        Shut down the gRPC server.

        This method stops the server with a grace period of 0 seconds,
        which means it will stop immediately without waiting for ongoing
        operations to complete.

        ### Notes

        : Uses a grace period of 0 seconds for immediate shutdown

        : Called automatically when handling Ctrl+C in serve() method or
            can be called directly to stop the server programmatically

        """
        self.server.stop(0)

    def serve(self):
        """
        Start the gRPC server and block until interrupted.

        This method starts the server, logs a message indicating the server
        is listening, and then blocks until interrupted by a signal, at which
        point it shuts down the server cleanly.

        ### Notes

        - This method is blocking and is typically called from a CLI command
        - The server URL is determined from the configuration
        - A running cement app converts SIGINT/SIGTERM into CaughtSignal, so
            both that and a plain KeyboardInterrupt are caught to stop cleanly

        ### Output

        : Logs the server URL to the application log

        """
        self.startup()
        self.app.log.info('Grpc server started, listening on ' + self._config('url'))
        try:
            self.server.wait_for_termination()
        except (KeyboardInterrupt, CaughtSignal):
            # cement turns SIGINT/SIGTERM into CaughtSignal, so a plain
            # KeyboardInterrupt rarely reaches here; catch both so the server
            # is always stopped cleanly on interruption
            self.shutdown()


class TokeoGrpcController(Controller):
    """
    Controller for managing gRPC services.

    This controller provides command-line commands for interacting with
    the gRPC server, such as starting the server with the 'serve' command.

    """

    class Meta:
        """Controller meta-data for command-line integration."""

        label = 'grpc'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'serve and manage grpc methods with tokeo grpc service'
        description = 'Manage grpc service and methods.'
        epilog = f'Example: {basename(argv[0])} grpc serve'

    def _setup(self, app):
        super(TokeoGrpcController, self)._setup(app)

    @ex(
        help='start grpc service',
        description='Spin up the grpc service.',
        arguments=[],
    )
    def serve(self):
        """
        Start the gRPC service.

        This command starts the gRPC server and blocks until interrupted.
        It delegates to the app.grpc.serve() method of the TokeoGrpc extension.
        """
        self.app.grpc.serve()


def tokeo_grpc_extend_app(app):
    """
    Extend the application with the gRPC extension.

    This function adds the gRPC handler to the application and initializes it,
    making it available as app.grpc.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    - This function is called during application setup

    - It creates the TokeoGrpc instance and attaches it to the app as app.grpc

    """
    app.extend('grpc', TokeoGrpc(app))
    app.grpc._setup(app)


def load(app):
    """
    Load the gRPC extension into the application.

    This function is the main entry point for the extension, called by Cement
    during the application initialization process.

    """
    app.handler.register(TokeoGrpcController)
    app.hook.register('post_setup', tokeo_grpc_extend_app)
