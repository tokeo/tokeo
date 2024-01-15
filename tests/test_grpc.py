from cedra.main import CedraTest


def test_grpc_count_words():
    # test command1 with arguments
    argv = ['grpc', 'count-words', '--url', 'https://github.com']
    with CedraTest(argv=argv) as app:
        app.run()
        assert app.last_rendered is None
