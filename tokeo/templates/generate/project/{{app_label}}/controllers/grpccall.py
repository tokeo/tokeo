"""
gRPC client controller for the {{ app_class_name }} service.

GrpcCallController exposes the remote gRPC methods as nested CLI commands
(stacked on the base controller). Each command parses its arguments, opens
a channel to the configured grpc url and calls the matching stub method.

### Notes:

- The channel target is read from the grpc config section (grpc.url), the
    same url the server binds to
- By default the channel is plain (grpc.insecure_channel); pass --tls to
    switch to a TLS channel. --cert/--key, --user or --insecure imply --tls
- Server cert validation: when TLS is active, the server PEM at config
    grpc.tls_certificate is used as the root CA. With --insecure (or when
    grpc.tls_certificate is null) a one-off TOFU probe via the stdlib ssl
    module fetches whatever cert the server presents and accepts it
- --cert and --key together enable mTLS (client cert auth); both must be
    given or neither
- --user/--password are sent as a standard HTTP-Basic 'authorization'
    metadata header (base64(user:password)); the server interceptor decides
    how to validate them. --user without --password sends an empty password,
    --password without --user is rejected
- ping is a sync smoke test: round-trips a small PingResponse, never
    dispatches a worker; useful to verify connectivity without Dramatiq
- count_words dispatches the count_words actor on the server and returns an
    empty response right away

"""

import base64
import ssl
from concurrent import futures  # noqa: F401
import grpc
from google.protobuf import empty_pb2
from cement import ex
from tokeo.ext.argparse import Controller
from tokeo.core.utils.controllers import controller_log_info_help
from ..core.exc import {{ app_class_name }}Error
from ..core.grpc import {{ app_label }}_servicer  # noqa: F401
from ..core.grpc.proto import {{ app_label }}_pb2
from ..core.grpc.proto import {{ app_label }}_pb2_grpc


# shared by every grpc command on this controller; spliced into the @ex args
_GRPC_AUTH_ARGS = [
    (
        ['--tls'],
        dict(
            action='store_true',
            default=False,
            help='open a TLS encrypted channel (default is insecure_channel)',
        ),
    ),
    (
        ['--insecure'],
        dict(
            action='store_true',
            default=False,
            help='trust the server cert as presented (TOFU; implies --tls)',
        ),
    ),
    (
        ['--cert'],
        dict(
            action='store',
            default=None,
            help='client cert PEM file for mTLS (requires --key; implies --tls)',
        ),
    ),
    (
        ['--key'],
        dict(
            action='store',
            default=None,
            help='client key PEM file for mTLS (requires --cert; implies --tls)',
        ),
    ),
    (
        ['--user'],
        dict(
            action='store',
            default=None,
            help='basic-auth user (implies --tls)',
        ),
    ),
    (
        ['--password'],
        dict(
            action='store',
            default=None,
            help='basic-auth password (requires --user; defaults to empty)',
        ),
    ),
]


class GrpcCallController(Controller):

    class Meta:
        label = 'grpccall'
        stacked_type = 'nested'
        stacked_on = 'base'

        # disable the ugly curly command doubled listening
        subparser_options = dict(metavar='')

        # text displayed at the top of --help output
        description = 'Call remote grpc methods.'

        # text displayed at the bottom of --help output
        epilog = 'Example: {{ app_label }} grpccall count-words --url value'

        # short help information
        help = 'call remote grpc methods manually'

    def _grpc_channel(self):
        """
        Build a gRPC channel from the --tls/--cert/--key/--user/--insecure
        flags on self.app.pargs.

        Returns a plain insecure channel unless TLS was requested explicitly
        or implied by --cert/--key, --user or --insecure. With TLS the root
        CA comes from grpc.tls_certificate; --insecure or a null
        tls_certificate falls back to a TOFU probe via ssl.

        ### Returns:

        - **grpc.Channel**: A context-manager-style channel; use in `with`

        ### Raises:

        - **{{ app_class_name }}Error**: When only one of --cert/--key is set

        """
        target = self.app.config.get('grpc', 'url')
        pargs = self.app.pargs

        # cert and key are paired for mTLS
        if bool(pargs.cert) != bool(pargs.key):
            raise {{ app_class_name }}Error(
                '--cert and --key must both be given for mTLS'
            )

        # any of these turns tls on; --tls alone is plain tls without mTLS/auth
        tls_enabled = (
            pargs.tls or pargs.insecure
            or pargs.cert is not None
            or pargs.user is not None
        )

        if not tls_enabled:
            return grpc.insecure_channel(target)

        # pick the root ca: server PEM from config, or TOFU probe
        server_pem = self.app.config.get('grpc', 'tls_certificate')
        if pargs.insecure or server_pem is None:
            # split host:port, ssl.get_server_certificate connects and returns
            # the cert pem; this is the trust-on-first-use moment
            host, _, port = target.rpartition(':')
            cert_pem = ssl.get_server_certificate((host, int(port)))
            root_ca = cert_pem.encode()
        else:
            with open(server_pem, 'rb') as f:
                root_ca = f.read()

        # optional mTLS client cert chain
        cli_cert = None
        cli_key = None
        if pargs.cert:
            with open(pargs.cert, 'rb') as f:
                cli_cert = f.read()
            with open(pargs.key, 'rb') as f:
                cli_key = f.read()

        creds = grpc.ssl_channel_credentials(
            root_certificates=root_ca,
            certificate_chain=cli_cert,
            private_key=cli_key,
        )
        return grpc.secure_channel(target, creds)

    def _grpc_metadata(self):
        """
        Build per-call invocation metadata for basic-auth from --user and
        --password on self.app.pargs.

        Returns None when --user is not given so callers can pass
        `metadata=self._grpc_metadata()` unconditionally.

        ### Returns:

        - **tuple|None**: Metadata tuple for the stub call, or None

        ### Raises:

        - **{{ app_class_name }}Error**: When --password is given without --user

        """
        pargs = self.app.pargs
        if pargs.user is None:
            if pargs.password is not None:
                raise {{ app_class_name }}Error('--password requires --user')
            return None
        # empty password is allowed; the interceptor decides what to do with it
        password = pargs.password if pargs.password is not None else ''
        token = base64.b64encode(
            f'{pargs.user}:{password}'.encode()
        ).decode()
        return (('authorization', f'Basic {token}'),)

    @ex(
        help='ping the grpc service (sync, no worker dispatch)',
        arguments=_GRPC_AUTH_ARGS,
    )
    def ping(self):
        # NOTE (from gRPC Python Team):
        # .close() is possible on a channel and should be used
        # in circumstances in which the with statement does
        # not fit the needs of the code.
        controller_log_info_help(self)
        with self._grpc_channel() as channel:
            stub = {{ app_label }}_pb2_grpc.{{ app_class_name }}Stub(channel)
            response = stub.Ping(
                empty_pb2.Empty(),
                metadata=self._grpc_metadata(),
            )
            self.app.log.info('  response: ' + response.message)

    @ex(
        help='call the CountWords method by grpc client',
        arguments=[
            (
                ['--url'],
                dict(
                    action='store',
                    required=True,
                    help='Url for the resource to get counted',
                ),
            ),
            *_GRPC_AUTH_ARGS,
        ],
    )
    def count_words(self):
        # NOTE (from gRPC Python Team):
        # .close() is possible on a channel and should be used
        # in circumstances in which the with statement does
        # not fit the needs of the code.
        controller_log_info_help(self)
        self.app.log.info('  given url: ' + self.app.pargs.url)
        with self._grpc_channel() as channel:
            stub = {{ app_label }}_pb2_grpc.{{ app_class_name }}Stub(channel)
            _ = stub.CountWords(
                {{ app_label }}_pb2.CountWordsRequest(url=self.app.pargs.url),
                metadata=self._grpc_metadata(),
            )
