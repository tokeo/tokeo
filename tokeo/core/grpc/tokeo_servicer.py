from tokeo.core import tasks
from proto import tokeo_pb2_grpc
from google.protobuf import empty_pb2


class TokeoServicer(tokeo_pb2_grpc.TokeoServicer):

    def CountWords(self, request, context):
        print('TokeoServicer CountWords called for: ', request.url)
        tasks.actors.count_words.send(request.url)
        return empty_pb2.Empty()
