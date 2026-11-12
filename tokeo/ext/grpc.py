"""
Serves a Tokeo application as gRPC server and calls other gRPC services as
client. It includes a controller for managing gRPC services and templates for
generating client/server implementations.

The extension handles server lifecycle management, dynamic loading of servicers,
and configuration management.

### Features

- Dynamically loads servicers and protocol buffer implementations from the
    configured module and class paths
- Manages the server lifecycle with clean startup and shutdown
- Adds CLI commands to run and control the gRPC server
- Logs through the application's logging system
- Reads workers, TLS, and interceptor options from the extension config
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
from tokeo.core.utils.modules import resolve_object
from tokeo.core.utils.tls import create_certificate
from tokeo.core.utils.validate import as_list, is_bool, is_filled_str, is_int_gt


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

    ### Args

    - **app** (Application): The Cement application instance
    - **args**: Variable length argument list passed to parent initializer

    ### Keyword Args

    - **kw**: Arbitrary keyword arguments passed to parent initializer

    ### Notes

    - The server is lazily initialized when first accessed through
        the ```server``` property
    - Server configuration is drawn from the application's 'grpc' config section
    - Provides methods for startup, shutdown, and serving (blocking mode)
    - Supports dynamic loading of servicer implementations

    """

    class Meta:
        """Extension meta-data."""

        # Label of this extension
        label = 'tokeo.grpc'

        # Name of the config section
        config_section = 'grpc'

        # Defaults of the config section
        config_defaults = dict(
            # address and port to run the service on
            address='localhost:50051',
            # number of concurrent workers
            max_workers=1,
            # module:method that adds the servicer to the server (generated
            # by protoc), mandatory
            proto_add_servicer_to_server=None,
            # module:class of the servicer that serves the calls, mandatory
            grpc_servicer=None,
            # module:method that returns the chain of interceptors, optional
            grpc_interceptor=None,
            # tls is opt-in; while disabled no tls code or library is touched
            tls=False,
            # custom cert: path to the pem file of the cert (both or none)
            tls_cert_path=None,
            # custom cert: path to the pem file of the key (both or none)
            tls_key_path=None,
            # validity for the in-memory auto cert when no custom cert is set
            tls_valid_days=90,
            # auto cert subject common name (cosmetic); None -> from address host
            tls_cert_cn=None,
            # auto cert extra names/ips for SAN (address host is always included)
            tls_cert_sans=None,
            # auto cert key: 'rsa' (size in bits) or 'ec' (size = curve 256/384),
            # grpc does no tls handshake with a P-521 key
            tls_key_type='ec',
            # auto cert key size, bits for rsa or the curve for ec
            tls_key_size=384,
            # client ca: when set, only clients with a valid cert of this ca
            # pass the tls handshake, the interceptors still decide each call
            tls_client_ca_path=None,
        )

    def __init__(self, app, *args, **kw):
        super().__init__(*args, **kw)
        #: The Cement application instance
        self.app = app
        self._server = None
        # the thread pool of the server, freed again by shutdown
        self._executor = None
        # the module:attr options, resolved when the server is built
        self._proto_add_servicer_to_server = None
        self._grpc_servicer = None
        self._grpc_interceptor = None

    def _setup(self, app):
        # the app handed to _setup must be the same instance this extension
        # was initialized with; guard against accidental mis-wiring
        if app is not self.app:
            raise TokeoGrpcError('_setup() received a different app than the one TokeoGrpc was initialized with')
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        self._proto_add_servicer_to_server = self._config('proto_add_servicer_to_server')
        if not self._proto_add_servicer_to_server:
            raise TokeoGrpcError('Missing mandatory proto_add_servicer_to_server option in config')
        self._grpc_servicer = self._config('grpc_servicer')
        if not self._grpc_servicer:
            raise TokeoGrpcError('Missing mandatory grpc_servicer option in config')
        # optional interceptor chain factory (module:method), None when unset
        self._grpc_interceptor = self._config('grpc_interceptor')

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a convenient wrapper around the application's config.get method,
        accessing values from the extension's config section.

        ### Args

        - **key** (str): Configuration key to retrieve

        ### Keyword Args

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
                max_workers count
            - Dynamically imports the protocol buffer module containing the
                add_servicer_to_server function
            - Dynamically imports the servicer class module
            - Resolves the optional interceptor chain from grpc_interceptor
                and passes it to the server at construction
            - Registers the servicer with the server using the
                add_servicer_to_server function
            - Binds the listen address, either as a TLS secured port when
                tls is set or as a plain insecure port otherwise

        : TLS is fully opt-in. With tls false the server never imports
            or calls any TLS code. With it true, a custom tls_cert_path /
            tls_key_path pair is used when both are given, otherwise an
            in-memory self signed cert (valid for tls_valid_days) is generated
            and never written to disk

        """
        if self._server is None:
            # resolve the optional interceptor chain before the server is
            # built, interceptors can only be passed at construction time
            interceptors = resolve_object(self._grpc_interceptor)() if self._grpc_interceptor else None
            # get dynamic methods for proto and service
            proto_add_servicer_to_server = resolve_object(self._proto_add_servicer_to_server)
            grpc_servicer = resolve_object(self._grpc_servicer)
            # create the server
            self._executor = futures.ThreadPoolExecutor(max_workers=self._config('max_workers'))
            try:
                server = grpc.server(self._executor, interceptors=interceptors)
                # bind the listen port; tls is opt-in and only that branch pulls
                # in any tls machinery, so a plain server never touches tls code
                tls = self._config('tls')
                if not is_bool(tls):
                    raise TokeoGrpcError(f'tls must be true or false, not {tls!r}')
                if tls:
                    server.add_secure_port(self._config('address'), self._build_tls_server_credentials())
                else:
                    server.add_insecure_port(self._config('address'))
                # append services
                proto_add_servicer_to_server(grpc_servicer(), server)
            except Exception as err:
                # a failed build ends its pool, the next try builds anew
                self._executor.shutdown(wait=False)
                self._executor = None
                if isinstance(err, TokeoGrpcError):
                    raise
                raise TokeoGrpcError(f'grpc server could not be built on {self._config("address")}: {err}') from err
            # keep the server only once it is complete, a failed build is retried
            self._server = server

        return self._server

    def _build_tls_server_credentials(self):
        """
        Build the gRPC server credentials for a TLS secured port.

        Two modes are selected by configuration: a custom certificate when
        both tls_cert_path and tls_key_path point to PEM files, otherwise an
        auto generated in-memory self signed certificate. When
        tls_client_ca_path is set, mutual TLS is enabled on top and every
        client must present a valid cert of that ca.

        ### Returns

        - **grpc.ServerCredentials**: Credentials to pass to add_secure_port

        ### Raises

        - **TokeoGrpcError**: If only one of tls_cert_path / tls_key_path is set

        ### Notes

        : The custom cert files (and the client ca, if any) are the only
            things read from disk; the auto cert path keeps everything in
            memory

        : mTLS is opt-in via tls_client_ca_path and then requires a client cert;
            grpc knows no optional client cert, without a ca it asks for none.
            The interceptors decide each call in both cases, with mTLS they
            also get the identity of the client cert

        """
        cert = self._config('tls_cert_path')
        key = self._config('tls_key_path')
        if cert and key:
            # custom cert: read the configured pem files as given
            with open(key, 'rb') as f:
                key_pem = f.read()
            with open(cert, 'rb') as f:
                cert_pem = f.read()
        elif cert or key:
            raise TokeoGrpcError('tls_cert_path and tls_key_path must both be set to use a custom certificate')
        else:
            # auto cert: generate a self signed certificate in memory
            key_pem, cert_pem = self._generate_self_signed_cert()
        # optional mtls: a client ca requires a valid client cert of it
        ca = self._config('tls_client_ca_path')
        if ca:
            with open(ca, 'rb') as f:
                ca_pem = f.read()
            return grpc.ssl_server_credentials(
                [(key_pem, cert_pem)],
                root_certificates=ca_pem,
                require_client_auth=True,
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

        - **TokeoGrpcError**: On a missing or unsupported tls_key_type, a
            tls_key_size or tls_valid_days that is no integer greater than 0,
            or an ec tls_key_size that is not one of 256, 384

        ### Notes

        : The certificate comes from ```create_certificate``` of
            ```tokeo.core.utils.tls```, which imports the cryptography package
            only then, so it stays out of the non-tls and custom-cert paths

        : The host from the address plus every tls_cert_sans entry become SANs,
            each added as an ip address when it parses as one, otherwise as a
            dns name; client validation uses these SANs, not the CN

        : ```tls_cert_cn``` only sets the (cosmetic) subject common name; when
            unset the address host is used

        """
        # derive the host from a "host:port" address (strip ipv6 brackets); the
        # host is both the cn fallback and the first san
        host = self._config('address').rsplit(':', 1)[0].strip('[]') or 'localhost'
        # private key: rsa by bit size, or ec by curve (256, 384); the tls of
        # grpc offers no P-521 signature, a P-521 server finds no common one
        key_type = self._config('tls_key_type')
        if not is_filled_str(key_type):
            raise TokeoGrpcError(f"tls_key_type must be set to 'rsa' or 'ec', not {key_type!r}")
        key_type = key_type.lower()
        key_size = self._config('tls_key_size')
        if not is_int_gt(key_size, 0):
            raise TokeoGrpcError(f'tls_key_size must be an integer greater than 0, not {key_size!r}')
        # validate a sensible key_size
        if key_type == 'rsa':
            if key_size < 2048:
                raise TokeoGrpcError(f'tls_key_size {key_size} is not valid for rsa (use 2048 or greater)')
        elif key_type == 'ec':
            if key_size not in (256, 384):
                raise TokeoGrpcError(f'tls_key_size {key_size} is not valid for ec (use 256 or 384, grpc does no handshake with 521)')
        else:
            raise TokeoGrpcError(f"tls_key_type '{key_type}' is not supported (use 'rsa' or 'ec')")

        valid_days = self._config('tls_valid_days')
        if not is_int_gt(valid_days, 0):
            raise TokeoGrpcError(f'tls_valid_days must be an integer greater than 0, not {valid_days!r}')

        # subject cn: tls_cert_cn override or the address host; sans from host
        # and tls_cert_sans, typed as ip or dns name by the generator
        cn = self._config('tls_cert_cn') or host
        sans = [entry for entry in [host, *as_list(self._config('tls_cert_sans'))] if entry]
        _, _, cert_pem, key_pem = create_certificate(cn, sans=sans, key_type=key_type, key_size=key_size, valid_days=valid_days)
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

        : Frees the thread pool of the server too, grpc leaves it running;
            its threads end once their work is done

        """
        self.server.stop(0)
        self._executor.shutdown(wait=False)

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
        self.app.log.info('Grpc server started, listening on ' + self._config('address'))
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
        """Controller meta-data."""

        label = 'grpc'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'serve and manage grpc methods with tokeo grpc service'
        description = 'Manage grpc service and methods.'
        epilog = f'Example: {basename(argv[0])} grpc serve'

    def _setup(self, app):
        super()._setup(app)

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

    This function adds the gRPC extension to the application and initializes it,
    making it available as app.grpc.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    : This function is called during application setup

    : It creates the TokeoGrpc instance and attaches it to the app as app.grpc

    """
    app.extend('grpc', TokeoGrpc(app))
    app.grpc._setup(app)


def tokeo_grpc_shutdown(app):
    """
    Shut the grpc server down when the app closes, if it was built.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    : Registered on ```pre_close```, so a server started by ```startup()```
        does not outlive ```app.close()```

    """
    if app.grpc._server is not None:
        app.grpc.shutdown()


def load(app):
    """
    Load the gRPC extension into the application.

    This function is the main entry point for the extension, called by Cement
    during the application initialization process.

    """
    app.handler.register(TokeoGrpcController)
    app.hook.register('post_setup', tokeo_grpc_extend_app)
    app.hook.register('pre_close', tokeo_grpc_shutdown)
