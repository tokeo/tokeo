"""
gRPC client controller for the {{ app_class_name }} service.

GrpcCallController exposes the remote gRPC methods as nested CLI commands
(stacked on the base controller). Each command parses its arguments, opens
an insecure channel to the configured grpc url, builds the matching request
and calls the stub method.

### Notes:

- The channel target is read from the grpc config section (grpc.url), the
    same url the server binds to
- The channel is opened via grpc.insecure_channel inside a with block, so it
    is closed automatically when the command returns
- count_words is the shipped example: it sends a CountWordsRequest built from
    --url to the CountWords stub method and ignores the empty response

"""

from concurrent import futures  # noqa: F401
import grpc
from cement import ex
from tokeo.ext.argparse import Controller
from tokeo.core.utils.controllers import controller_log_info_help
from ..core.grpc import {{ app_label }}_servicer  # noqa: F401
from ..core.grpc.proto import {{ app_label }}_pb2_grpc  # noqa: F401
from ..core.grpc.proto import {{ app_label }}_pb2  # noqa: F401


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
        # NOTE (from gRPC Python Team):
        # .close() is possible on a channel and should be used
        # in circumstances in which the with statement does
        # not fit the needs of the code.
        controller_log_info_help(self)
        self.app.log.info('  given url: ' + self.app.pargs.url)
        with grpc.insecure_channel(self.app.config.get('grpc', 'url')) as channel:
            stub = {{ app_label }}_pb2_grpc.{{ app_class_name }}Stub(channel)
            _ = stub.CountWords({{ app_label }}_pb2.CountWordsRequest(url=self.app.pargs.url))
