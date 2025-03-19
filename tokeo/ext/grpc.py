"""
Tokeo gRPC Extension Module.

This module provides gRPC integration for Tokeo applications, allowing Tokeo apps
to serve as gRPC servers and clients. It includes a controller for managing gRPC
services and templates for generating client/server implementations.

The extension handles server lifecycle management, dynamic loading of servicers,
and configuration management.

### Features:

- **Dynamic loading** of servicers and protocol buffer implementations based on configuration
- **Server lifecycle management** with clean startup and shutdown
- **CLI commands** for managing the gRPC server
- **Integration** with the application's logging system
- **Customization** through comprehensive configuration settings
- **Template generation** for new Tokeo projects

"""

from sys import argv
from os.path import basename
from tokeo.ext.argparse import Controller
from cement.core.meta import MetaMixin
from cement import ex
from concurrent import futures
import grpc
import importlib


class TokeoGrpc(MetaMixin):
    """
    The TokeoGrpc extension integrates gRPC server within Tokeo applications.

    This class manages the gRPC server lifecycle and configuration.
    It dynamically loads the gRPC servicer and protocol buffer implementations
    based on configuration, providing a seamless integration between
    Tokeo applications and gRPC services.

    ### Notes:

    - The server is lazily initialized when first accessed through the `server` property
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
            url='localhost:50051',
            max_worker=1,
            proto_add_servicer_to_server='proto.module:add_servicer_to_server',
            grpc_servicer='tokeo.core.grpc.tokeo_servicer:TokeoServicer',
        )

    def __init__(self, app, *args, **kw):
        super(TokeoGrpc, self).__init__(*args, **kw)
        self.app = app
        self._server = None
        self._proto_add_servicer_to_server_module = ''
        self._proto_add_servicer_to_server_method = ''
        self._grpc_servicer_module = ''
        self._grpc_servicer_method = ''

    def _setup(self, app):
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        a = self._config('proto_add_servicer_to_server').split(':')
        self._proto_add_servicer_to_server_module = a[0]
        self._proto_add_servicer_to_server_method = a[1]
        a = self._config('grpc_servicer').split(':')
        self._grpc_servicer_module = a[0]
        self._grpc_servicer_method = a[1]

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a convenient wrapper around the application's config.get method,
        accessing values from the extension's config section.

        ### Args:

        - **key** (str): Configuration key to retrieve
        - **kwargs**: Additional arguments passed to config.get()

        ### Returns:

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

        ### Returns:

        - **grpc.Server**: The configured gRPC server instance

        ### Notes:

        : The initialization process follows these steps:

            1. Creates a server with ThreadPoolExecutor using configured max_worker count
            1. Dynamically imports the protocol buffer module containing the add_servicer_to_server function
            1. Dynamically imports the servicer class module
            1. Registers the servicer with the server using the add_servicer_to_server function
            1. Configures the server to listen on the specified URL

        """
        if self._server is None:
            self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=self._config('max_worker')))
            # get dynamic methods for proto and service
            proto_add_servicer_to_server_module = importlib.import_module(self._proto_add_servicer_to_server_module)
            proto_add_servicer_to_server_method = getattr(proto_add_servicer_to_server_module, self._proto_add_servicer_to_server_method)
            grpc_servicer_module = importlib.import_module(self._grpc_servicer_module)
            grpc_servicer_method = getattr(grpc_servicer_module, self._grpc_servicer_method)
            # append services
            proto_add_servicer_to_server_method(grpc_servicer_method(), self._server)
            self._server.add_insecure_port(self._config('url'))

        return self._server

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

        ### Notes:

        : Uses a grace period of 0 seconds for immediate shutdown

        : Called automatically when handling Ctrl+C in serve() method or
          can be called directly to stop the server programmatically
        """
        self.server.stop(0)

    def serve(self):
        """
        Start the gRPC server and block until interrupted.

        This method starts the server, logs a message indicating the server
        is listening, and then blocks until a keyboard interrupt is received,
        at which point it shuts down the server cleanly.

        ### Notes:

        1. This method is blocking and is typically called from a CLI command

        1. The server URL is determined from the configuration

        ### Output:

        : Logs the server URL to the application log

        """
        self.startup()
        self.app.log.info('Grpc server started, listening on ' + self._config('url'))
        try:
            while True:
                self.server.wait_for_termination()
        except KeyboardInterrupt:
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

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

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


"""
## Template Usage Documentation

The gRPC extension provides templates for implementing both gRPC server and client
components in new Tokeo projects.

### Server Components:

The AppServicer class implements the gRPC service defined in the proto file:

```python
class AppServicer(app_pb2_grpc.AppServicer):
    def CountWords(self, request, context):
        app.print('AppServicer CountWords called for: ', request.url)
        tasks.actors.count_words.send(request.url)
        return empty_pb2.Empty()
```

The servicer:

- Imports necessary protocol buffer modules (generated from .proto files)
- Accesses the global app instance via tokeo.ext.appshare
- Implements service methods defined in the .proto file
- Can dispatch tasks to background workers using Dramatiq

### Client Components:

The GrpcCallController provides CLI commands for invoking gRPC methods:

The controller:

- Provides human-friendly command-line interface to gRPC methods
- Handles command-line argument parsing and validation
- Creates an insecure gRPC channel to connect to the server
- Constructs the appropriate request objects and makes RPC calls

### Adding New Services:

To add a new gRPC service method:

1. Define the method in your .proto file
2. Generate the Python code using the protoc compiler
3. Implement the method in your ServicerClass
4. Create a controller command for client-side access if needed

"""
