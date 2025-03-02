![tokeo-social](https://github.com/tokeo/tokeo/assets/410087/ea3cb6f6-7aec-49e1-b622-a01dbf89508b)

<br/>

<h1 align="center">tokeo</h1>

<p align="center">
  <strong>Unleashing the Power of Python, Cement, Dramatiq, Apschedule and Grpc for Superior EDA Solutions!</strong>
</p>
<p align="center">
-- Event-Driven Architecture, Backend-Automation, Event-Driven Automation --
</p>

<br/>

<br/>

In an an Event-Driven Architecture ([EDA](https://en.wikipedia.org/wiki/Event-driven_architecture)) world you need a fast and reliable development cycle even for small projects.

[Cement](https://builtoncement.com) is a well known [python](https://www.python.org) project and allows to build fast and super documented CLI apps with easyness.

The [Dramatiq](https://dramatiq.io) background task processing library in combination with [RabbitMQ Message Broker](https://www.rabbitmq.com) runs a reliable and solid EDA environment.

An integrated [Grpc](https://grpc.io) service gives access to the available tasks and workflows from outside.

For timed jobs excution there is an integrated interactive [Apscheduler](https://apscheduler.readthedocs.io/en/master/) cron service.

Last but not least expose your values and functions via [Nicegui](https://nicegui.io/) web based api and pages.

Kickstart your EDA projects with **tokeo** and experience a seamless development cycle.

Cheers<br/>
Tom

<br/>

## tokeo CLI and extensions come with all the workers, services, tasks, jobs and functions

<br/>

### Create a new project for development

```bash
$ mkdir path/for/project

$ cd path/for/project

$ python -m venv .venv

$ .venv/bin/pip install git+https://github.com/tokeo/tokeo.git@master

$ .venv/bin/tokeo generate project . # use --defaults to create "inspire" with defaults

$ make venv

$ source .venv/bin/activate

$ make dev

$ make proto # if feature_grpc installed

$ the_app --help # verify setup
```

<br/>

### Run the message broker workers

```bash
### run the dramatiq workers for the implemented tasks
$ the_app dramatiq serve
```

<br/>

### Run a task by emitter example

```bash
### run the count_words task
$ the_app emit count-words --url https://github.com

### check result output on dramatiq serve console
```

<br/>

### Run the grpc service

```bash
### run the grpc service for the exported methods
$ the_app grpc serve
```

<br/>

### Run a task by grpc call

```bash
### run the count_words task
$ the_app grpc-client count-words --url https://github.com

### check result output on dramatiq serve console
```

<br/>

### Run tasks by scheduler

```bash
### run the scheduler
$ the_app scheduler launch --interactive --paused

### now you are in the interactive scheduler shell

Scheduler> list
### will print the active running tasks and their next execution time

### check result output on dramatiq serve console
```

<br/>

### Run the automate service

```bash
### run the automate service for the exported tasks
$ the_app automate run uname --verbose
```

<br/>

## Controlling log level

The log level for the app can be set by config file (`config/the_app.yaml`) or an environment variable.

```bash
### this enables the app debug level output
$ THE_APP_LOG_COLORLOG_LEVEL=debug the_app command

### Instead this enables also the framework debug log
$ the_app --debug command

### At least this enables framework debug log only
$ CEMENT_LOG=1 the_app command
```

<br/>

## Development

This project includes a number of helpers in the `Makefile` to streamline common development tasks.

<br/>

### Makefile

```bash
# show outdated packages
$ make outdated

# clean all temporary
$ make clean

# create and update the grpc codes for proto files
$ make proto

# use pyink for formatting
$ make fmt # can be filtered by sources=path/files

# use flake8 as linter
$ make lint # can be filtered by sources=path/files

# run your tests
$ make test # can be filtered by files=path/files
```

<br/>

## Deployments

### create installation packages

```bash
# create a a source package (tgz)
make sdist

# create a wheel package (whl)
make wheel
```

<br/>

### Docker

Included is a basic `Dockerfile` for building and distributing `The app`,
and can be built with the included `make` helper:

```
$ make docker

$ docker run the_app --help
```
