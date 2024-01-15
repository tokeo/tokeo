from cedra.core import tasks
from proto import cedra_pb2_grpc
from google.protobuf import empty_pb2


class CedraServicer(cedra_pb2_grpc.CedraServicer):

    def CountWords(self, request, context):
        print('CedraServicer CountWords called for: ', request.url)
        tasks.actors.count_words.send(request.url)
        return empty_pb2.Empty()
