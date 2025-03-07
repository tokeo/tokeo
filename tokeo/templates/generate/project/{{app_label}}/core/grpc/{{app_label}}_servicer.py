from google.protobuf import empty_pb2
from tokeo.ext.appshare import app
from {{ app_label }}.core import tasks
from .proto import {{ app_label }}_pb2  # noqa: F401
from .proto import {{ app_label }}_pb2_grpc  # noqa: F401


class {{ app_class_name }}Servicer({{ app_label }}_pb2_grpc.{{ app_class_name }}Servicer):

    def CountWords(self, request, context):
        app.print('{{ app_class_name }}Servicer CountWords called for: ', request.url)
        tasks.actors.count_words.send(request.url)
        return empty_pb2.Empty()
