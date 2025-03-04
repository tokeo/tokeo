"""
Tokeo gRPC Extension Module

This module provides gRPC integration for Tokeo applications, allowing Tokeo apps
to serve as gRPC servers and clients. It includes a controller for managing gRPC
services and templates for generating client/server implementations.

The extension handles server lifecycle management, dynamic loading of servicers,
and configuration management.

Example:
    ```python
    # In your app configuration
    app.config['grpc']['url'] = 'localhost:50051'
    app.config['grpc']['max_worker'] = 4

    # To start the gRPC server
    app.grpc.serve()

    # For client usage (from a controller method)
    with grpc.insecure_channel(self.app.config.get('grpc', 'url')) as channel:
        stub = app_pb2_grpc.AppStub(channel)
        response = stub.MethodName(app_pb2.MethodRequest(param='value'))
    ```

The templates provided with this extension include:
- A servicer implementation (AppServicer) that handles incoming requests
- A client controller (GrpcCallController) that provides CLI commands
    for invoking gRPC methods
- Proto file integration for type definitions and service contracts

For new projects, these templates are customized with the appropriate application
name and can be further extended with additional methods as needed.
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
    based on configuration.

    The server is lazily initialized when first accessed through the `server`
    property, and configured using values from the application configuration.

    Attributes:
        app: The Cement application instance
        _server: The internal gRPC server instance
        _proto_add_servicer_to_server_module: Module path for
            the add_servicer_to_server function
        _proto_add_servicer_to_server_method: Method name for
            the add_servicer_to_server function
        _grpc_servicer_module: Module path for the servicer class
        _grpc_servicer_method: Class name for the servicer
    """

    class Meta:
        """
        Extension meta-data for TokeoGrpc.

        This inner class defines configuration metadata for the extension,
        including default configuration values.

        Attributes:
            label: Unique identifier for this handler
            config_section: Id for configuration section
            config_defaults: Dictionary with default configuration values
        """

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
        """
        Initialize the TokeoGrpc extension.

        Args:
            app: The Cement application instance
            *args: Variable length argument list
            **kw: Arbitrary keyword arguments
        """
        super(TokeoGrpc, self).__init__(*args, **kw)
        self.app = app
        self._server = None
        self._proto_add_servicer_to_server_module = ''
        self._proto_add_servicer_to_server_method = ''
        self._grpc_servicer_module = ''
        self._grpc_servicer_method = ''

    def _setup(self, app):
        """
        Set up the TokeoGrpc extension.

        This method initializes configuration and parses module and method paths
        from the configuration. It splits the module:method strings into separate
        components for later dynamic loading.

        Args:
            app: The Cement application instance
        """
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        a = self._config('proto_add_servicer_to_server').split(':')
        self._proto_add_servicer_to_server_module = a[0]
        self._proto_add_servicer_to_server_method = a[1]
        a = self._config('grpc_servicer').split(':')
        self._grpc_servicer_module = a[0]
        self._grpc_servicer_method = a[1]

    def _config(self, key, **kwargs):
        """
        Get a configuration value from the gRPC section.

        This is a simple wrapper, and is equivalent to:
            ``self.app.config.get(<section>, <key>)``.

        Args:
            key: The configuration key to retrieve
            **kwargs: Additional parameters to pass to app.config.get()

        Returns:
            The configuration value
        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    @property
    def server(self):
        """
        Get the gRPC server instance, creating it if needed.

        This property lazily initializes the gRPC server when first accessed.
        The initialization process:
        1. Creates a server with ThreadPoolExecutor using configured
            max_worker count
        2. Dynamically imports the protocol buffer module containing
            the add_servicer_to_server function
        3. Dynamically imports the servicer class module
        4. Registers the servicer with the server using
            the add_servicer_to_server function
        5. Configures the server to listen on the specified URL

        Returns:
            grpc.Server: The configured gRPC server instance
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
        """
        self.server.stop(0)

    def serve(self):
        """
        Start the gRPC server and block until interrupted.

        This method:
        1. Starts the server by calling startup()
        2. Logs a message indicating the server is listening on the configured URL
        3. Enters a loop that blocks until the server terminates
        4. Handles KeyboardInterrupt (Ctrl+C) by shutting down the server

        This is typically called from a command-line controller to run
        the server as a standalone process.
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

    It extends the Cement Controller class and is registered with the
    application during the extension loading process.
    """

    class Meta:
        """
        Controller meta-data for command-line integration.

        Attributes:
            label: Command name in the CLI
            stacked_type: Type of controller stacking
            stacked_on: Parent controller to stack on
            subparser_options: Options for command-line parsing
            help: Short help text displayed in command listings
            description: Longer description displayed in help output
            epilog: Text displayed after the help text
        """

        label = 'grpc'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'serve and manage grpc methods with tokeo grpc service'
        description = 'Manage grpc service and methods.'
        epilog = f'Example: {basename(argv[0])} grpc serve'

    def _setup(self, app):
        """
        Set up the controller.

        Args:
            app: The Cement application instance
        """
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

    This function:
    1. Creates a new TokeoGrpc instance
    2. Adds it to the application as 'grpc'
    3. Sets up the extension

    Args:
        app: The Cement application instance
    """
    app.extend('grpc', TokeoGrpc(app))
    app.grpc._setup(app)


def load(app):
    """
    Load the gRPC extension into the application.

    This is the main entry point for the extension, called by Cement
    during the application initialization process.

    This function:
    1. Registers the TokeoGrpcController with the application
    2. Registers a hook to extend the application with the gRPC extension

    Args:
        app: The Cement application instance
    """
    app.handler.register(TokeoGrpcController)
    app.hook.register('post_setup', tokeo_grpc_extend_app)


"""
Template Usage Documentation

The gRPC extension provides templates for implementing both gRPC server and client
components in new Tokeo projects. These templates are located at:
- tokeo/templates/generate/project/{{app_label}}/core/grpc/{{app_label}}_servicer.py
- tokeo/templates/generate/project/{{app_label}}/controllers/grpccall.py

## Server Components ({{app_label}}_servicer.py)

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

## Client Components (grpccall.py)

The GrpcCallController provides CLI commands for invoking gRPC methods:

```python
class GrpcCallController(Controller):
    class Meta:
        label = 'grpccall'
        stacked_type = 'nested'
        stacked_on = 'base'
        description = 'Call remote grpc methods.'
        help = 'call remote grpc methods manually'

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
        ],
    )
    def count_words(self):
        with grpc.insecure_channel(self.app.config.get('grpc', 'url')) as channel:
            stub = app_pb2_grpc.AppStub(channel)
            response = stub.CountWords(
                app_pb2.CountWordsRequest(url=self.app.pargs.url)
            )
```

The controller:
- Provides human-friendly command-line interface to gRPC methods
- Handles command-line argument parsing and validation
- Creates an insecure gRPC channel to connect to the server
- Constructs the appropriate request objects and makes RPC calls
"""
