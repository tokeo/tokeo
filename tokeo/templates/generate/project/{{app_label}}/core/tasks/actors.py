"""
Business task actors for distributed processing with Dramatiq.

This module provides a centralized location for defining business-specific
Dramatiq actors in Tokeo and Cement applications. Actors are distributed
task processors that can handle asynchronous workloads through message passing.

### Features:

- **Centralized actor definitions** for maintainable code organization
- **Queue-based processing** with configurable timeouts and retries
- **Rate limiting** through the app.dramatiq.locks interface
- **Integration with application context** via app-specific business logic

### Usage:

Register actors with the @app.dramatiq.actor decorator:

```python
from tokeo.ext.appshare import app

@app.dramatiq.actor(queue_name="default")
def process_business_data(item_id):
    # Application-specific business logic
    pass
```

For rate-limited operations that need controlled concurrency:

```python
@app.dramatiq.actor(queue_name="api_operations")
@app.dramatiq.locks.throttle(count=5, per_seconds=60)
def process_business_data(item_id):
    # Rate-limited to 5 calls per minute across all workers
    pass
```

### Notes:

- Tasks defined here will be discovered automatically when dramatiq workers
    are started via the 'dramatiq serve' command
- Define app.dramatiq.actors in your application's config file to point to
    this module
- Actors should handle serialization/deserialization of complex data structures

"""

import requests
from tokeo.ext.appshare import app  # noqa: F401


@app.dramatiq.actor(queue_name='count_words')
def count_words(url):
    """
    Actor function to asynchrouniously: 'count words on a webpage'.

    This example implements the delegated word counting in an asynchronous
    worker using Dramatiq.

    ### Args:

    - **url** (str, optional): URL of the webpage to analyze.
        Argument is given by the worker from RabbitMQ on start.
        Value was set on putting the task on the queue.

    """
    try:
        response = requests.get(url)
        count = len(response.text.split(' '))
        app.log.info(f'There are {count} words at {url!r}.')
    except Exception as err:
        app.log.error(f'Could not count words: {err}')
