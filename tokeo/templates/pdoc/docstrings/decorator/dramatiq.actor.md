The actor decorator runs this function as a background task: calling
```send``` puts a message into RabbitMQ, a worker of ```dramatiq serve``` runs
the function.

### Args

- **queue_name** (str, optional): The queue of its messages. A queue with
    ```_unparalleled``` in its name runs one task at a time. Defaults to
    ```default```.
- **actor_name** (str, optional): A unique name of the actor. Defaults to the
    name of the function.
- **priority** (int, optional): Order among messages a worker already
    fetched, lower runs first. Defaults to 0.

### Keyword Args

- **max_retries** (int, optional): Retries after a failure. Defaults to the
    config, 20.
- **min_backoff** (int, optional): Delay of the first retry in milliseconds,
    it about doubles with each retry. Defaults to the config, 15000.
- **max_backoff** (int, optional): Longest delay of a retry in milliseconds.
    Defaults to the config, 604800000 (7 days).
- **retry_when** (callable, optional): Decides on a retry instead of
    ```max_retries```
- **throws** (tuple, optional): Expected exceptions, not retried
- **time_limit** (int, optional): Longest run in milliseconds. Defaults to
    the config, 600000.
- **max_age** (int, optional): Messages older than this in milliseconds are
    not run. Defaults to the config, no limit.

### Raises

- **TokeoDramatiqConnectionError**: If RabbitMQ is not reachable on send

### Example

```python
# run in the background
func.send(42)

# run after a delay of 1 min
func.send_with_options(args=(42,), delay=60000)

# overtake waiting messages, a higher priority first (needs rabbitmq.max_priority)
func.send_with_options(args=(42,), broker_priority=9)
```
