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

## 🚀 Accelerate Your Python Development

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

## 💪 Why Choose Tokeo?

Tokeo combines a robust CLI framework with task automation, message queues, and web interfaces, making it ideal for Python backend projects. Key features include:

- **Integrated EDA Stack**: Combines Dramatiq, RabbitMQ, and gRPC for efficient task processing and external access, plus APScheduler for scheduled jobs.
- **Flexible Task Automation**: Use Fabric-based tools (`tokeo.ext.automate`) to define and run local or remote tasks, with flexible configuration via YAML and CLI overrides.
- **Extensible CLI**: Built on Cement, Tokeo supports custom commands and plugins, simplifying complex workflows with minimal setup.
- **Developer-Friendly Tools**: The `Makefile` provides one-liners for formatting (`fmt`), linting (`lint`), testing (`test`), and packaging (`sdist`, `wheel`), speeding up development.
- **DiskCache** by `tokeo.ext.diskcache` enhances performance with disk-based caching for frequently accessed data, reducing load times and improving efficiency.
- **Manage task execution rates** using `temper` and `throttle` to prevent overloading with rate-limiting tools, ensuring stable and controlled processing.
- **SMTP with Jinja2 Templates**: Send emails with precise, individualized content using Jinja2 templates, supporting text, HTML, inline images, and attachments for dynamic communications.
- **Simple debugging** when using `app.inspect`. Provides basic debugging tools to inspect application state of vars and objects.
- **Web Interface**: Create beautiful UIs with the built-in NiceGUI extension to visualize data and interact with your application.

Whether you're building microservices, automating workflows, or prototyping, Tokeo provides the structure and flexibility to get started quickly.

<br/>

## 🛠️ Getting Started

### Installing Tokeo

```bash
# Install directly from Pypi.org
pip install tokeo

# Verify installation
tokeo --help
```

### Creating a New Project

Set up a Tokeo project in minutes:

```bash
# Define a dedicated space for your project
cd basepath/for/project

# Generate a new project (interactive prompts)
tokeo generate project your_app

# Or use defaults for quick setup
tokeo generate project your_app --defaults
```

### Use our docker container to create a New Project

Set up a Tokeo project in seconds:

```bash
# Define a dedicated space for your project
cd basepath/for/project

# Use docker image to generate a new project (interactive prompts)
docker run -it -v $(pwd)/your_app:/your_app tokeocli/tokeo generate project /your_app
```

### Setting Up Your Development Environment

```bash
# Enter the dedicated space for your project
cd your_app

# Prepare Python virtual environment
make venv

# Activate the virtual environment
source .venv/bin/activate

# Install development dependencies
make dev

# If using feature gRPC, generate code from proto files
make proto

# Verify your application is working
your_app --help
```

<br/>

## 📊 Exploring Tokeo Features

### Process Background Tasks with Dramatiq (needs a running RabbitMQ)

```bash
# Launch Dramatiq workers to process background tasks
your_app dramatiq serve

# Trigger a task (e.g., count-words)
your_app emit count-words --url https://github.com
```

### Expose Services via gRPC

```bash
# Start the gRPC server for external task access
your_app grpc serve

# Execute a task using the gRPC client
your_app grpc-client count-words --url https://github.com
```

### Schedule Recurring Tasks

```bash
# Run the scheduler with interactive shell
your_app scheduler launch

# Within the scheduler shell, list and manage tasks
Scheduler> list
Scheduler> tasks pause 1 2 3  # Pause task with ID 1, 2, 3
Scheduler> tasks resume 1  # Resume task with ID 1
Scheduler> tasks fire 1  # Resume task with ID 1
```

### Automate Deployment and Operations

```bash
# Run automation tasks locally or remotely
your_app automate run uname --verbose
your_app automate run deploy --target=production
```

### Create Web Interfaces

```bash
# Start the web interface
your_app nicegui serve

# Access the interface at http://localhost:4123
```

<br/>

## 🧰 Developer Tools

Tokeo includes comprehensive tools to maintain code quality:

```bash
# Format your code
make fmt

# Run linting checks
make lint

# Run tests
make test

# Run tests with coverage report
make test cov=1

# Build documentation
make doc

# Check for outdated dependencies
make outdated
```

<br/>

## 🚀 Deployment Options

### Package Your Application

```bash
# Create source distribution
make sdist

# Create wheel package
make wheel

# Build Docker image
make docker
```

### Control Logging

Set log levels via config, environment variables, or CLI flags:

```bash
# App debug logs
YOUR_APP_LOG_COLORLOG_LEVEL=debug your_app command

# App + framework debug logs
your_app --debug command

# Framework debug logs only
CEMENT_LOG=1 your_app command
```

<br/>

## 📚 Project Structure

When you create a new project with Tokeo, you get a clean, modular structure:

- `config/` - Configuration files for prod, stage, dev and test environments
- `your_app/controllers/` - Command-line interface controllers
- `your_app/core/logic` - Space for your core application logic
- `your_app/core/grpc/` - gRPC service definitions and implementations
- `your_app/core/pages/` - Web interface pages and apis
- `your_app/core/tasks/` - All implementations of actors, agents, automation, operations, performers and others
- `your_app/core/utils/` - A place to put your overall tools and helper functions
- `tests/` - Test suite to ensure reliability

<br/>

## 🔮 Next Steps

Tokeo is designed to grow with your project. As you build, consider:

- Customizing the application structure for your specific needs
- Creating new controllers for additional commands
- Adding task processors for background workloads
- Designing web interfaces to visualize data
- Implementing automated deployment pipelines

Explore the [Makefile](https://github.com/tokeo/tokeo/blob/master/Makefile) and [extensions](https://github.com/tokeo/tokeo/tree/master/tokeo/ext) for more tools and customization options. Checkout the example project at [Tokeo Spiral example](https://github.com/tokeo/spiral). Tokeo's modular design makes it easy to adapt for your backend needs.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
