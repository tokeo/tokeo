def config_defaults():
    # application configuration defaults
    return dict(
        cedra=dict(
            debug=False,
        ),
        rabbitmq=dict(
            url='amqp://user:pass@localhost:5672/vhost',
        ),
        worker=dict(
            processes=1,
            threads=1,
        ),
    )
