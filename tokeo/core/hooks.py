from tokeo.core.dramatiq import broker


def hook_dramatiq_setup(app):
    # call the broker registration
    broker.register(app)
