"""
Tokeo Dramatiq Extension Module.

Dramatiq integration for asynchronous task processing in Tokeo applications.

Wraps Dramatiq behind a Tokeo extension: a RabbitMQ broker subclass with a
queue-naming convention for single-active-consumer queues, a Cement
controller that drives ```dramatiq``` workers from the application config, and
optional integration with the diskcache locks for cross-worker rate limiting.

### Features

- Single-active-consumer queues via the '_unparalleled' name suffix
    (RabbitMQ x-single-active-consumer flag set on declare)

- Distributed locks and rate limiting on actors when the diskcache
    extension is loaded (best-effort: missing without diskcache)

- ```dramatiq serve``` controller with worker process/thread counts and
    optional --watch path for actor hot-reload during development

- pdoc render hooks that swap the runtime decorator for a doc-friendly
    stub so generated docs show ```@dramatiq.actor``` signatures cleanly

### Example

```python
from tokeo.ext.appshare import app

# Register an actor
@app.dramatiq.actor(queue_name="default")
def process_data(data_id):
    # This will be executed by workers asynchronously
    results = perform_complex_calculations(data_id)
    store_results(results)

# Enqueue a task
process_data.send(42)

# For rate-limited actors, use the locks
@app.dramatiq.actor(queue_name="api_calls")
@app.dramatiq.locks.throttle(count=10, per_seconds=60)
def call_external_api(resource_id):
    # Limited to 10 calls per minute across all workers
    return make_api_request(resource_id)
```

"""

import dramatiq
from dramatiq import actor as dramatiq_actor
from tokeo.ext.dramatiq.base import (  # noqa: F401
    TokeoDramatiqError,
    TokeoDramatiqConnectionError,
    TokeoActor,
    tokeo_actor,
    TokeoAsyncIO,
    ExtendedRabbitmqBroker,
    TokeoDramatiq,
)
from tokeo.ext.dramatiq.controller import TokeoDramatiqController


def tokeo_dramatiq_pdoc_pre_render(app):
    """
    Replace the dramatiq decorator with a simple one for pdoc rendering.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    : This function replaces the complex Dramatiq actor decorator
        with a simpler version that pdoc can handle

    """
    from tokeo.core.utils.pdoc import pdoc_replace_decorator

    dramatiq.actor = pdoc_replace_decorator
    if hasattr(app.dramatiq, 'actor'):
        app.dramatiq.actor = pdoc_replace_decorator


def tokeo_dramatiq_pdoc_post_render(app):
    """
    Restore the original dramatiq decorator after pdoc rendering.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    : This function restores the original Dramatiq actor decorator functionality

    """
    dramatiq.actor = dramatiq_actor
    if hasattr(app.dramatiq, 'actor'):
        app.dramatiq.actor = tokeo_actor


def tokeo_dramatiq_pdoc_render_decorator(app, func, decorator, args, kwargs):
    """
    Handle docstrings for dramatiq decorators in pdoc.

    This function provides custom handling for Dramatiq actor decorators
    during pdoc documentation rendering.

    ### Args

    - **app** (Application): The Cement application instance
    - **func** (function): The function being decorated
    - **decorator** (str): The decorator string
    - **args** (list): Positional arguments to the decorator

    ### Keyword Args

    - **kwargs** (dict): Keyword arguments to the decorator

    ### Returns

    - **dict|None**: Dictionary with decorator information or None if not handled

    ### Notes

    - This function extracts queue_name parameters from decorators to
        provide meaningful documentation

    - It works with both the global dramatiq.actor and app.dramatiq.actor forms

    """
    if decorator == '@dramatiq.actor' or decorator == '@app.dramatiq.actor':
        params = None
        if kwargs is not None and 'queue_name' in kwargs:
            try:
                value = kwargs['queue_name'].value
                params = f'queue_name="{value}"' if isinstance(value, str) else f'queue_name={value}'
            except Exception:
                params = 'queue_name=...'
        return dict(
            decorator=decorator,
            params=params,
            docstring=app.pdoc.docstrings('decorator', 'dramatiq.actor'),
        )


def tokeo_dramatiq_extend_app(app):
    """
    Initialize and register the Dramatiq extension with the application.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    - This function is called during application setup

    - It creates the TokeoDramatiq instance and attaches it to the app
        as app.dramatiq

    """
    app.extend('dramatiq', TokeoDramatiq(app))
    app.dramatiq._setup(app)


def tokeo_dramatiq_shutdown(app):
    """
    Perform cleanup when shutting down the Dramatiq extension.

    This function closes broker connections and performs other cleanup tasks
    when the application is shutting down.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    - Called during application shutdown to properly close connections

    - Important for clean application termination without resource leaks

    """
    app.dramatiq.close()


def load(app):
    """
    Load the Dramatiq extension into the application.

    This function is called by Cement when loading extensions. It defines hooks
    for application integration, registers the controller for command-line access,
    and sets up initialization and shutdown hooks.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    - Registers the TokeoDramatiqController for CLI commands
    - Defines extension-specific hooks that can be used by other extensions
    - Sets up integration with pdoc for documentation generation

    """
    # Define hooks that can be used by other extensions
    app.hook.define('tokeo_dramatiq_pre_start')
    app.hook.define('tokeo_dramatiq_post_end')
    # Register the command-line controller
    app.handler.register(TokeoDramatiqController)
    # Register initialization and shutdown hooks
    app.hook.register('post_setup', tokeo_dramatiq_extend_app)
    app.hook.register('pre_close', tokeo_dramatiq_shutdown)
    # register for pdoc
    app.hook.register('tokeo_pdoc_pre_render', tokeo_dramatiq_pdoc_pre_render)
    app.hook.register('tokeo_pdoc_post_render', tokeo_dramatiq_pdoc_post_render)
    app.hook.register('tokeo_pdoc_render_decorator', tokeo_dramatiq_pdoc_render_decorator)
