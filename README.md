![tokeo-social](https://github.com/tokeo/tokeo/assets/410087/ea3cb6f6-7aec-49e1-b622-a01dbf89508b)

<br/>

<h1 align="center">tokeo</h1>

<p align="center">
  <strong>Unleashing the Power of Python, Cement, Dramatiq, APScheduler, and gRPC for Superior EDA Solutions!</strong>
</p>
<p align="center">
  -- Event-Driven Architecture, Backend Automation, Event-Driven Automation --
</p>

<br/>

<br/>

In an Event-Driven Architecture ([EDA](https://en.wikipedia.org/wiki/Event-driven_architecture)) world, you need a fast and reliable development cycle, even for small projects.

[Cement](https://builtoncement.com) is a well-known [Python](https://www.python.org) project that enables building fast and well-documented CLI apps with ease.

The [Dramatiq](https://dramatiq.io) background task processing library, paired with [RabbitMQ Message Broker](https://www.rabbitmq.com), provides a reliable and solid EDA environment.

An integrated [gRPC](https://grpc.io) service allows external access to available tasks and workflows.

For timed job execution, a background and interactive [APScheduler](https://apscheduler.readthedocs.io/en/master/) cron service is included.

[Fabric](https://www.fabfile.org) simplifies running task automation locally and remotely, configurable through YAML files and CLI arguments.

Expose your values and functions via [NiceGUI](https://nicegui.io/) web-based API and pages.

Kickstart your EDA projects with **tokeo** and experience a seamless development cycle.

Cheers<br/>
Tom

<br/>

## Why Choose Tokeo?

Tokeo combines a robust CLI framework with task automation, message queues, and web interfaces, making it ideal for Python backend projects. Key features include:

- **Integrated EDA Stack**: Combines Dramatiq, RabbitMQ, and gRPC for efficient task processing and external access, plus APScheduler for scheduled jobs.
- **Flexible Task Automation**: Use Fabric-based tools (`tokeo.ext.automate`) to define and run local or remote tasks, with flexible configuration via YAML and CLI overrides.
- **Extensible CLI**: Built on Cement, Tokeo supports custom commands and plugins, simplifying complex workflows with minimal setup.
- **Developer-Friendly Tools**: The `Makefile` provides one-liners for formatting (`fmt`), linting (`lint`), testing (`test`), and packaging (`sdist`, `wheel`), speeding up development.
- **DiskCache** by `tokeo.ext.diskcache` enhances performance with disk-based caching for frequently accessed data, reducing load times and improving efficiency.
- **Manage task execution rates** using `temper` and `throttle` to prevent overloading with rate-limiting tools, ensuring stable and controlled processing.
- **SMTP with Jinja2 Templates**: Send emails with precise, individualized content using Jinja2 templates, supporting text, HTML, inline images, and attachments for dynamic communications.
- **Simple debugging** when using `app.inspect`. Provides basic debugging tools to inspect application state of vars and objects.

Whether you’re building microservices, automating workflows, or prototyping, Tokeo provides the structure and flexibility to get started quickly.

<br/>

## Create a New Project

Set up a Tokeo project for development:

```bash
# create a space for the project
$ mkdir path/for/project

# enter new project dir
$ cd path/for/project

# create a simple venv to install the tokeo generator
$ python -m venv .venv

# install the necessary tool-chain
$ .venv/bin/pip install git+https://github.com/tokeo/tokeo.git@master

# run and create a new project from template
# use --defaults to create "inspire" with defaults
$ .venv/bin/tokeo generate project .

# prepare python venv
$ make venv

# activate the venv
$ source .venv/bin/activate

# Start development and install packages
$ make dev

# Generate gRPC code (if feature_grpc is enabled)
$ make proto

# start the app and verify
$ the_app --help
```

<br/>

## Run Message Broker Workers

Launch Dramatiq workers to process background tasks:

```bash
$ the_app dramatiq serve
```

<br/>

## Emit a Task

Trigger a task (e.g., `count-words`) via the CLI:

```bash
$ the_app emit count-words --url https://github.com
# Check output in the dramatiq serve console
```

<br/>

## Run the gRPC Service

Start the gRPC server for external task access:

```bash
$ the_app grpc serve
```

<br/>

## Call a Task via gRPC

Execute a task using the gRPC client:

```bash
$ the_app grpc-client count-words --url https://github.com
# Check output in the dramatiq serve console
```

<br/>

## Schedule Tasks

Run the scheduler for timed jobs:

```bash
$ the_app scheduler launch --interactive --paused
# Enter the interactive shell
Scheduler> list  # Show active tasks and next run times
# Check output in the dramatiq serve console
```

<br/>

## Automate Tasks

Run automation tasks locally or remotely with Fabric:

```bash
$ the_app automate run uname --verbose  # Display system info
$ the_app automate run svc              # Manage services (requires config)
```

<br/>

## Control Logging

Set log levels via config (`config/the_app.yaml`), environment variables, or CLI flags:

```bash
$ THE_APP_LOG_COLORLOG_LEVEL=debug the_app command  # App debug logs
$ the_app --debug command                           # App + framework debug logs
$ CEMENT_LOG=1 the_app command                      # Framework debug logs only
```

<br/>

## Development Tools

Tokeo includes a `Makefile` and extensions to streamline development:

### Makefile Commands

```bash
$ make venv        # Set up virtualenv and dependencies
$ make outdated    # List outdated packages
$ make clean       # Remove temporary files
$ make proto       # Generate gRPC code from .proto files
$ make fmt         # Format code with pyink (optional: sources=path/files)
$ make lint        # Lint with flake8 (optional: sources=path/files)
$ make test        # Run tests with pytest (optional: files=path/files)
$ make coverage    # Generate test coverage report
```

<br/>

## Deployment

### Build Packages

Create distributable packages:

```bash
$ make sdist  # Source package (.tar.gz)
$ make wheel  # Wheel package (.whl)
```

### Docker

Build and run with Docker:

```bash
$ make docker       # Build the Docker image
$ docker run the_app --help  # Verify the image
```

<br/>

## Next Steps

Explore the [Makefile](https://github.com/tokeo/tokeo/blob/master/Makefile) and [extensions](https://github.com/tokeo/tokeo/tree/master/tokeo/ext) for more tools and customization options. Tokeo’s modular design makes it easy to adapt for your backend needs.

<br/>
<br/>

<p>
  tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
</p>
