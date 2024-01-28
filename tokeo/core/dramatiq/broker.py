import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq import middleware


def setup():
    # the import within the function broker_setup has been moved
    # to the inside of the function to avoid circular imports
    from tokeo.main import Tokeo
    # instantiate a Tokeo() app to get config etc. when starting as broker module via dramatiq
    app = Tokeo()
    # run some of the _setup_handlers to get a functional App object
    app._setup_extension_handler()
    app._setup_config_handler()
    app._setup_log_handler()
    app._setup_arg_handler()
    app._setup_output_handler()

    # setup the broker, middlewares and register
    register(app)


def register(app):
    # re-build set of middlewares to use
    use_middleware = [
        m()
        for m in [
            middleware.AgeLimit,
            middleware.TimeLimit,
            middleware.ShutdownNotifications,
            middleware.Callbacks,
            middleware.Pipelines,
            middleware.Retries,
        ]
    ]
    # create the broker to RabbitMQ based on config
    rabbitmq_broker = RabbitmqBroker(
        url=app.config.get('rabbitmq', 'url'),
        middleware=use_middleware,
    )
    # globally set the broker to dramtiq
    dramatiq.set_broker(rabbitmq_broker)
