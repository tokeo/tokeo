import socket
import pytest
from {{ app_label }}.main import {{ app_class_name }}Test


def _grpc_server_reachable(app):
    # count-words is a real client call: it needs the project's grpc server
    # listening on the configured url, so probe it instead of failing hard
    url = app.config.get('grpc', 'url')
    host, _, port = url.partition(':')
    try:
        with socket.create_connection((host or 'localhost', int(port or 50051)), timeout=1):
            return True
    except OSError:
        return False


def test_grpc_count_words():
    # test the count-words client command with arguments
    argv = ['grpc', 'count-words', '--url', 'https://github.com']
    with {{ app_class_name }}Test(argv=argv) as app:
        if not _grpc_server_reachable(app):
            pytest.skip('grpc server is not reachable -- start it to run this test')
        app.run()
        assert app.last_rendered is None
