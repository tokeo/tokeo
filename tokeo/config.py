def config_defaults():
    # application configuration defaults
    return dict(
        tokeo=dict(
            debug=False,
        ),
        rabbitmq=dict(
            url='amqp://guest:guest@localhost:5672/',
        ),
        worker=dict(
            processes=2,
            threads=1,
        ),
        grpc=dict(
            url='localhost:50051',
            worker_threads=2,
        ),
    )
