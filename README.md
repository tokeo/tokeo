![tokeo-social](https://github.com/tokeo/tokeo/assets/410087/ea3cb6f6-7aec-49e1-b622-a01dbf89508b)

<br/>

# tokeo
## Unleashing the Power of Cement, Dramatiq, and Grpc for Superior EDA Solutions!

<br/>

In an an Event-Driven Architecture ([EDA](https://en.wikipedia.org/wiki/Event-driven_architecture)) world you need a fast and reliable development cycle even for small projects.

[Cement](https://builtoncement.com) is a well known [python](https://www.python.org) project and allows to build fast and super documented CLI apps with easyness.

The [Dramatiq](https://dramatiq.io) background task processing library in combination with [RabbitMQ Message Broker](https://www.rabbitmq.com) runs a reliable and solid EDA environment.

An integrated [Grpc](https://grpc.io) service gives access to the available tasks and workflows from outside.

Kickstart your EDA projects with **tokeo** and experience a seamless development cycle.

Cheers<br/>
Tom

<br/>

# tokeo CLI handles all the workers, services, tasks, jobs and functions

<br/>

## Installation on production

```bash
$ make virtualenv

$ source .venv/bin/activate

$ python setup.py develop # currently install will cause an error on lazy-load
```

<br/>

## Run the message broker workers

```bash
$ source .venv/bin/activate

### run the dramatiq workers for the implemented tasks

$ tokeo dramatiq serve
```

<br/>

## Run a task by emitter

```bash
$ source .venv/bin/activate

### run the count_words task

$ tokeo emit count-words --url https://github.com

### check result output on dramatiq serve console
```

<br/>

## Run the grpc service

```bash
$ source .venv/bin/activate

### run the grpc service for the exported methods

$ tokeo grpc serve
```

<br/>

## Run a task by grpc call

```bash
$ source .venv/bin/activate

### run the count_words task

$ tokeo grpc-client count-words --url https://github.com

### check result output on dramatiq serve console
```

<br/>

## Controlling log level

The log level for the app can be set by config file (`config/tokeo.yaml`) or an environment variable.

```bash
### this enables the app debug level output
$ TOKEO_LOG_COLORLOG_LEVEL=debug tokeo command

### Instead this enables also the framework debug log
$ tokeo --debug command

### At least this enables framework debug log only
$ CEMENT_LOG=1 tokeo command
```

<br/>

## Development

This project includes a number of helpers in the `Makefile` to streamline common development tasks.

<br/>

### Environment Setup

The following demonstrates setting up and working with a development environment:

```bash
### create a virtualenv for development

$ make virtualenv

$ source .venv/bin/activate

$ pip install -r requirements-dev.txt

$ python setup.py develop


### check tokeo cli application

$ tokeo --help


### run pytest / coverage

$ make test
```

<br/>

### Releasing to PyPi

Before releasing to PyPi, you must configure your login credentials:

**~/.pypirc**:

```
[pypi]
username = YOUR_USERNAME
password = YOUR_PASSWORD
```

Then use the included helper function via the `Makefile`:

```
$ make dist

$ make dist-upload
```

<br/>

## Deployments

<br/>

### Docker

Included is a basic `Dockerfile` for building and distributing `The tokeo`,
and can be built with the included `make` helper:

```
$ make docker

$ docker run -it tokeo --help
```
