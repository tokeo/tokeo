from concurrent import futures
from cement import Controller, ex
from tokeo.core.grpc import tokeo_servicer
from proto import tokeo_pb2_grpc
from proto import tokeo_pb2
import grpc


class Grpc(Controller):

    class Meta:
        label = 'grpc'
        stacked_type = 'nested'
        stacked_on = 'base'

        # text displayed at the top of --help output
        description = 'Manage grpc service and methods.'

        # text displayed at the bottom of --help output
        epilog = 'Example: tokeo grpc serve --option --param value'

    @ex(
        help='spin up the grpc service',
        arguments=[],
    )
    def serve(self):
        self.app.log.info('Spinning up grpc service ...')
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=self.app.config.get('grpc', 'worker_threads')))
        tokeo_pb2_grpc.add_TokeoServicer_to_server(tokeo_servicer.TokeoServicer(), server)
        server.add_insecure_port(self.app.config.get('grpc', 'url'))
        server.start()
        self.app.log.info('Tokeo grpc server started, listening on ' + self.app.config.get('grpc', 'url'))
        try:
            while True:
                server.wait_for_termination()
        except KeyboardInterrupt:
            server.stop(0)

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