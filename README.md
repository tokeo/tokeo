![tokeo-social](https://github.com/tokeo/tokeo/assets/410087/ea3cb6f6-7aec-49e1-b622-a01dbf89508b)

<br/>

<h1 align="center">tokeo</h1>

<p align="center">
  <strong>Unleashing the Power of Python, Cement, Dramatiq, APScheduler, and gRPC for Superior EDA Solutions!</strong>
</p>
<p align="center">
  -- Event-Driven Architecture, Backend Automation, Governed AI Agents --
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

Secrets stay encrypted at rest: the ```tokeo.ext.vault``` config handler resolves ```!vault:``` tagged values transparently on read -- your YAML never holds a plaintext credential.

And with ```tokeo.ext.ai```, your application speaks to AI providers through one governed runtime: typed contracts, guarded tool execution, full traces -- the same agent pipeline from a 1.5 MB local micro model up to any large provider.

Kickstart your EDA projects with **tokeo** and experience a seamless development cycle.

**Checkout &nbsp; [Spiral](https://github.com/tokeo/spiral)** &nbsp; 🍒 &nbsp; It takes you on an interactive journey through Tokeo's capabilities, providing a
fully functional environment where you can witness Event-Driven Architecture in real-time.

Cheers<br/>
Tom

<br/>

## 💪 Why Choose Tokeo?

Tokeo is a robust CLI framework for task automation, message queues, and web interfaces, making it ideal for Python backend projects. Key features include:

- **Integrated EDA Stack**: Combines Dramatiq, RabbitMQ, and gRPC for efficient task processing and external access, plus APScheduler for scheduled jobs.
- **Governed AI Agents**: A provider-agnostic AI runtime (```tokeo.ext.ai```) with typed contracts, profiles, and agents as plain configuration -- every tool call passes a guard pipeline (validate, policy, audit) and leaves a full trace.
- **Encrypted Secrets in Config**: The vault extension (```tokeo.ext.vault```) keeps credentials encrypted inside your YAML (```!vault:<profile>``` tags, built-in ```enc``` and ```scrypt``` handlers, keys from the environment) and decrypts them transparently at the leaf -- consumer code never changes, plaintext never lands in the config.
- **Flexible Task Automation**: Use Fabric-based tools (```tokeo.ext.automate```) to define and run local or remote tasks, with flexible configuration via YAML and CLI overrides.
- **Extensible CLI**: Built on Cement, Tokeo supports custom commands and plugins, simplifying complex workflows with minimal setup.
- **Developer-Friendly Tools**: The ```Makefile``` provides one-liners for formatting (```fmt```), linting (```lint```), testing (```test```), and packaging (```sdist```, ```wheel```), speeding up development.
- **DiskCache** by ```tokeo.ext.diskcache``` enhances performance with disk-based caching for frequently accessed data, reducing load times and improving efficiency.
- **Manage task execution rates** using ```temper``` and ```throttle``` to prevent overloading with rate-limiting tools, ensuring stable and controlled processing.
- **SMTP with Jinja2 Templates**: Send emails with precise, individualized content using Jinja2 templates, supporting text, HTML, inline images, and attachments for dynamic communications.
- **Simple debugging** when using ```app.inspect```. Provides basic debugging tools to inspect application state of vars and objects.
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
docker run -it -v $(pwd)/your_app:/src tokeocli/tokeo generate project /src
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

## 📊 Exploring Tokeo Features on New Project

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

### Automate Operations

```bash
# Run automation tasks locally or remotely
your_app automate run uname --verbose --as-json
```

### Create Web Interfaces

```bash
# Start the web interface
your_app nicegui serve

# Access the interface at http://localhost:4123
```

### Ask an AI Agent

```bash
# Ask through the default profile (mock provider, no external service needed)
your_app ai ask "ping"

# Run the guarded agent: every tool call passes validate, policy, audit
your_app ai ask "add 14 days to 2026-06-08" --profile akili --agent guarded
```

### Use Diskcache

```bash
# List content
your_app cache list

# Set value
your_app cache set counter --value 1 --value-type int

# Get value
your_app cache get counter
```

<br/>

## 🤖 Agents with Guardrails

The newest part of Tokeo is a complete, small AI agent runtime -- built on one conviction: **the model plans, the pipeline governs, the tools compute**. No step is implicit, every step is inspectable. It is deliberately compact, fully typed, and tested end to end: the same suite drives the mock provider, the guard pipeline, and a real trained model.

- **Contracts first**: Messages, tool calls, results, and traces are typed values (```tokeo.core.ai```), independent of any provider SDK.
- **Agents are configuration**: An agent is a named guard chain in YAML. ```audited``` records everything and forbids nothing; ```guarded``` adds validation and policy (e.g. a readonly filesystem). Lean by default: with ```agent: null``` requests run plain and untraced -- you opt into governance.
- **Tools are plain functions**: Registered with a spec, activated in groups (calendar, filesystem, mathematics) per profile. The provider never executes anything itself; results return as feedback through the guards.
- **Late binding, honestly tested**: Providers are resolved by class path. The built-in ```mock``` provider makes the whole pipeline testable without any external service -- the contracts are the product.

### akili -- the train-first micro LLM lab

[Spiral](https://github.com/tokeo/spiral) ships ```akili```, a complete and teachable micro language model that plans calendar tool calls -- small enough to read in an afternoon, real enough to prove the contracts end to end:

- **378,240 parameters, ~1.5 MB** -- byte-level tokenizer, 3 transformer blocks, NumPy-only inference in tens of milliseconds, no GPU and no service.
- **Train first, no shipped weights**: ```python -m spiral.core.akili.train``` creates the model on your machine (CPU is fine) with an honest held-out evaluation.
- **The language is data**: every word and sentence pattern lives in ```AKILI-LEX.yaml``` -- teaching the model new language (English and German today) is editing a file and retraining. An ablation switch (```--no-minus```) demonstrates the core lesson live: capability lives in the data, not in the code.
- **Grammar-constrained planning**: a byte-level automaton makes malformed plans impossible -- the model chooses *which* legal continuation, never *whether* to be legal.
- **Taught, not just documented**: ```AKILI-LLM.md``` explains training, the anatomy of the weights, and constrained decoding with detailed diagrams.

### The road ahead

The runtime is deliberately provider-shaped: the contracts you write against today are the contracts the next stages plug into.

1. **OpenAI-compatible provider** -- cloud endpoints and local runtimes (Ollama, LM Studio) behind the very same agents and guards; switching providers becomes editing a profile, not a refactor -- with the API key ```!vault:``` encrypted right inside it.
2. **Response guards** -- redact and truncate as after-guards: governance for what comes back, not only for what gets called.
3. **Sandboxed tool execution** -- tools run isolated (subprocess first, stronger isolation opt-in), so even a misbehaving tool stays inside its fence.
4. **Code mode** -- a ```python_exec``` tool that lets agents compose vetted tool calls as Python instead of many chat round-trips.

The contracts stay; the providers scale.

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

<br/>

## 📚 Project Structure

When you create a new project with Tokeo, you get a clean, modular structure:

- ```config/``` - Configuration files for prod, stage, dev and test environments
- ```your_app/core/logic``` - Space for your core application logic
- ```your_app/core/tasks/``` - Implementations of actors, agents, automations, operations, performers etc.
- ```your_app/core/ai/``` - Your AI providers and plain-function tools behind the guarded contracts
- ```your_app/core/akili/``` - The train-first micro LLM lab: model, lexicon (```AKILI-LEX.yaml```), teaching docs
- ```your_app/core/grpc/``` - gRPC service definitions and implementations
- ```your_app/core/utils/``` - A place to put your overall tools and helper functions
- ```your_app/controllers/``` - Command-line interface controllers
- ```your_app/site/``` - Web interface pages and apis
- ```your_app/templates/``` - Templates for rendering content
- ```tests/``` - Test suite to ensure reliability

<br/>

## 🔮 Next Steps

Tokeo is designed to grow with your project. As you build, consider:

- Customizing the application structure for your specific needs
- Creating new controllers for additional commands
- Adding task processors for background workloads
- Designing web interfaces to visualize data
- Wiring your own AI provider behind the same guarded agent contracts
- Implementing automated deployment pipelines

<br/>
<br/>

## ⭐ Support the Project

Tokeo is built in the open, with working code over promises: a governed agent runtime you can read in an afternoon, and a micro model you train yourself to prove the contracts end to end. If this approach is useful to you, a star on [GitHub](https://github.com/tokeo/tokeo) helps others find it -- issues, ideas, and pull requests are just as welcome.

One note on contributions: we do not accept purely AI-generated issues or pull requests. We keep the human in the loop and use AI as an exoskeleton, not as a replacement -- the same conviction that shapes the runtime itself.

<br/>
<br/>

**Checkout the example project at [Tokeo Spiral](https://github.com/tokeo/spiral)** and explore the [Makefile](https://github.com/tokeo/tokeo/blob/master/Makefile) and [extensions](https://github.com/tokeo/tokeo/tree/master/tokeo/ext) for more tools and customization options.  Tokeo's modular design makes it easy to adapt for your backend needs.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
