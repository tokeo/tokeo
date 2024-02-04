from tokeo.ext.argparse import Controller
from tokeo.core.grpc import tokeo_servicer
from cement import ex
from concurrent import futures
from proto import tokeo_pb2_grpc
from proto import tokeo_pb2
import grpc


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
        epilog = 'Example: tokeo grpc count-words --url value'

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
        # NOTE(gRPC Python Team): .close() is possible on a channel and should be
        # used in circumstances in which the with statement does not fit the needs
        # of the code.
        self.app.log.info('Try to call CountWords by grpc ...')
        with grpc.insecure_channel(self.app.config.get('grpc', 'url')) as channel:
            stub = tokeo_pb2_grpc.TokeoStub(channel)
            response = stub.CountWords(tokeo_pb2.CountWordsRequest(url=self.app.pargs.url))
