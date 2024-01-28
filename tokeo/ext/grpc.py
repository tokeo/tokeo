from sys import argv
from os.path import basename
from cement.core.meta import MetaMixin
from cement import Controller, ex
from concurrent import futures
import grpc
import importlib


class TokeoGrpc(MetaMixin):

    class Meta:

        """Extension meta-data."""

        #: Unique identifier for this handler
        label = 'tokeo.grpc'

        #: Id for config
        config_section = 'grpc'

        #: Dict with initial settings
        config_defaults = dict(
            url='localhost:50051',
            max_worker=1,
            proto_add_service_to_server='proto.module:add_service_to_server',
            grpc_service_handler='tokeo.core.grpc.tokeo_service_handler:TokeoGrpcServiceHandler',
        )

    def __init__(self, app, *args, **kw):
        super(TokeoGrpc, self).__init__(*args, **kw)
        self.app = app
        self._server = None
        self._proto_add_service_to_server_module = ''
        self._proto_add_service_to_server_method = ''
        self._grpc_service_handler_module = ''
        self._grpc_service_handler_method = ''

    def _setup(self, app):
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        a = self._config('proto_add_service_to_server').split(':')
        self._proto_add_service_to_server_module = a[0]
        self._proto_add_service_to_server_method = a[1]
        a = self._config('grpc_service_handler').split(':')
        self._grpc_service_handler_module = a[0]
        self._grpc_service_handler_method = a[1]

    def _config(self, key, default=None):
        """
        This is a simple wrapper, and is equivalent to: ``self.app.config.get(<section>, <key>)``.
        """
        return self.app.config.get(self._meta.config_section, key)

    @property
    def server(self):
        if self._server is None:
            self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=self._config('max_worker')))
            # get dynamic methods for proto and service
            proto_add_service_to_server_module = importlib.import_module(self._proto_add_service_to_server_module)
            proto_add_service_to_server_method = getattr(
                proto_add_service_to_server_module, self._proto_add_service_to_server_method
            )
            grpc_service_handler_module = importlib.import_module(self._grpc_service_handler_module)
            grpc_service_handler_method = getattr(grpc_service_handler_module, self._grpc_service_handler_method)
            # append services
            proto_add_service_to_server_method(grpc_service_handler_method(), self._server)
            self._server.add_insecure_port(self._config('url'))

        return self._server

    def startup(self):
        self.server.start()

    def shutdown(self):
        self.server.stop(0)

    def serve(self):
        self.startup()
        self.app.log.info('Grpc server started, listening on ' + self._config('url'))
        try:
            while True:
                self.server.wait_for_termination()
        except KeyboardInterrupt:
            self.shutdown()


class TokeoGrpcController(Controller):

    class Meta:
        label = 'grpc'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'serve and manage grpc methods with tokeo grpc service'
        description = 'Manage grpc service and methods.'
        epilog = f'Example: {basename(argv[0])} grpc serve\n '

    def _setup(self, app):
        super(TokeoGrpcController, self)._setup(app)

    def _default(self):
        self._parser.print_help()

    @ex(
        help='serve',
        description='Spin up the grpc service',
        epilog='',
        arguments=[],
    )
    def serve(self):
        self.app.grpc.serve()


def tokeo_grpc_extend_app(app):
    app.extend('grpc', TokeoGrpc(app))
    app.grpc._setup(app)


def load(app):
    app.handler.register(TokeoGrpcController)
    app.hook.register('post_setup', tokeo_grpc_extend_app)
