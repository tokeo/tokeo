from tokeo.main import TokeoTest


def test_grpc_count_words():
    # test command1 with arguments
    argv = ['grpc', 'count-words', '--url', 'https://github.com']
    with TokeoTest(argv=argv) as app:
        app.run()
        assert app.last_rendered is None
