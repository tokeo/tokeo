The actor decorator runs this function asynchronously as a background task.

Actors can also be scheduled and priorized by using `send_with_options`.

### Actor Configuration:

- **queue_name** (str): The queue where the task is placed
- **actor_name** (str): A unique name for the actor
- **max_retries** (int): Maximum number of retries if the task fails (_5_)
- **min_backoff** (int): Minimum backoff time in milliseconds before retry (_1000 ms_)
- **max_backoff** (int): Maximum backoff time in milliseconds before retry (_10000 ms_)
- **retry_when** (callable): Condition to retry the task (when an exception occurs)
- **throws** (tuple): Expected exceptions that the actor may raise (_Exception_)
- **max_age** (int): Maximum time in seconds before the task expires (_3600 s_)
- **time_limit** (int): Maximum execution time in milliseconds (_5000 ms_)

### Usage:

1. **Normal execution**:

    ```
    func.send(42)
    ```

1. **Scheduling execution with options**:

    ```
    func.send_with_options(args=(42,), delay=60000)  # Run after 1 min
    ```

1. **Adjusting priority**:

    ```
    func.send_with_options(args=(42,), priority=10)  # Higher priority
    ```

### Raises:

- **Exception**:

    If an error occurs during email sending.
