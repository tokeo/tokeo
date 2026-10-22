The actor decorator runs this function as a background task: calling
```send``` puts a message into RabbitMQ, a worker of ```dramatiq serve``` runs
the function.

### Actor Configuration

- **queue_name** (str): The queue of its messages (_default_); a queue with
  _unparalleled in its name runs one task at a time
- **actor_name** (str): A unique name of the actor (_the function name_)
- **priority** (int): Order among messages a worker already fetched, lower
  runs first (_0_)
- **max_retries** (int): Retries after a failure (_config, 20_)
- **min_backoff** (int): Delay of the first retry in milliseconds, it about
  doubles with each retry (_config, 15000 ms_)
- **max_backoff** (int): Longest delay of a retry in milliseconds (_config,
  7 days_)
- **retry_when** (callable): Decides on a retry instead of max_retries
- **throws** (tuple): Expected exceptions, not retried
- **time_limit** (int): Longest run in milliseconds (_config, 600000 ms_)
- **max_age** (int): Messages older than this in milliseconds are not run
  (_config, no limit_)

### Usage

1. **Run in the background**:

    ```
    func.send(42)
    ```

1. **Run after a delay**:

    ```
    func.send_with_options(args=(42,), delay=60000)  # after 1 min
    ```

1. **Overtake waiting messages** (needs rabbitmq.max_priority):

    ```
    func.send_with_options(args=(42,), broker_priority=9)  # higher first
    ```

### Raises

- **TokeoDramatiqConnectionError**:
    If RabbitMQ is not reachable on send.
