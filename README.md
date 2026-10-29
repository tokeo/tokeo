![tokeo-header](https://repository-images.githubusercontent.com/141847312/eeb77d15-04db-453f-9a4d-7b57697a828f)
<div align="right">image AI created with gemma 4 prompt</div>

<br/>

<h1 align="center">tokeo</h1>

<p align="center">
  <strong>Unleashing the Power of Python, Cement, Dramatiq, APScheduler, and gRPC for Superior EDA Solutions!</strong>
</p>
<p align="center">
  Event-Driven Architecture, Backend Automation, Governed AI Agents
</p>

<br/>

## 🚀 Accelerate Your Python Development

In an Event-Driven Architecture ([EDA](https://en.wikipedia.org/wiki/Event-driven_architecture)) world, you need a fast and reliable development cycle, even for small projects.

[Cement](https://builtoncement.com) is a well-known [Python](https://www.python.org) project for building fast and well-documented CLI apps with ease.

The [Dramatiq](https://dramatiq.io) background task processing library, paired with [RabbitMQ Message Broker](https://www.rabbitmq.com), provides a reliable and solid EDA environment.

An integrated [gRPC](https://grpc.io) service allows external access to available tasks and workflows.

For timed job execution, a background and interactive [APScheduler](https://apscheduler.readthedocs.io/en/master/) cron service is included.

Receive mails right inside your application with the smtpd service, an asyncio port of the battle-tested Ruby [midi-smtp-server](https://github.com/4commerce-technologies-AG/midi-smtp-server) that turns incoming SMTP messages into events.

[Fabric](https://www.fabfile.org) simplifies running task automation locally and remotely, configurable through YAML files and CLI arguments.

Expose your values and functions via [NiceGUI](https://nicegui.io/) web-based API and pages.

Secrets stay encrypted at rest: the ```tokeo.ext.vault``` config handler resolves ```!vault:``` tagged values transparently on read, so your YAML never holds a plaintext credential.

And when you want AI in the mix, the optional [tokeo-fundi](https://github.com/tokeo/fundi) add-on lets your application speak to AI providers through one governed runtime with typed contracts, guarded tool execution and full traces. The same agent pipeline runs from a small local micro model up to any large provider.

Kickstart your EDA projects with tokeo and experience a seamless development cycle.

Check out [Spiral](https://github.com/tokeo/spiral), the reference application. It is a working environment that shows the event-driven architecture and all features of Tokeo at work.

Cheers<br/>
Tom

<br/>

## Why Choose Tokeo?

Tokeo is a robust CLI framework for task automation, message queues, and web interfaces, making it ideal for Python backend projects. Its key features:

- Combines Dramatiq, RabbitMQ and gRPC for efficient task processing and external access, plus APScheduler for scheduled jobs.
- Adds governed AI agents with the optional [tokeo-fundi](https://github.com/tokeo/fundi) extension, a provider-agnostic AI runtime with typed contracts, profiles and agents as plain configuration. Every tool call passes a guard pipeline (validate, policy, audit) and leaves a full trace. ```tokeo-fundi``` is installed separately and entirely optional.
- Keeps credentials encrypted inside your YAML with the vault extension (```tokeo.ext.vault```): ```!vault:<profile>``` tags, built-in ```enc``` and ```scrypt``` handlers and keys from the environment. Values are decrypted transparently at the leaf, consumer code never changes and plaintext never lands in the config.
- Defines and runs local or remote tasks with the Fabric-based ```tokeo.ext.automate```, configured by YAML and CLI overrides.
- Builds on Cement, tokeo supports custom commands and plugins, simplifying complex workflows with minimal setup.
- Provides one-liners in the ```Makefile``` for formatting (```fmt```), linting (```lint```), testing (```test```) and packaging (```sdist```, ```wheel```), speeding up deployment.
- Enhances performance with disk-based caching for frequently accessed data (```tokeo.ext.diskcache```), reducing load times and improving efficiency.
- Manages task execution rates using ```temper``` and ```throttle``` to prevent overloading with rate-limiting tools, ensuring stable and controlled processing.
- Runs mail-receiving endpoints with the smtpd extension (```tokeo.ext.smtpd```) and dispatches the SMTP dialog to your own event handlers, with STARTTLS, AUTH, PROXY, limits, message spooling and scaling.
- Sends emails with individualized content using Jinja2 templates, supporting text, HTML, inline images and attachments for dynamic communications.
- Provides simple debugging with ```app.inspect```, basic tools to inspect the application state of vars and objects.
- Creates stylish UIs with the built-in NiceGUI extension to visualize data and interact with your application.

Whether you're building microservices, automating workflows, or prototyping, Tokeo provides the structure and flexibility to get started quickly.

<br/>

## Getting Started

### Installing Tokeo

```bash
# Install directly from Pypi.org
pip install tokeo

# Or add the optional governed AI runtime
pip install tokeo tokeo-fundi

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

## Trying the Features in a New Project

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
# Run the scheduler in the background, the default
your_app scheduler launch

# Run the scheduler with the interactive shell in the foreground
your_app scheduler launch --shell

# Within the scheduler shell, list and manage tasks
Scheduler> list
Scheduler> tasks pause 1 2 3  # Pause tasks with ID 1, 2, 3
Scheduler> tasks resume 1  # Resume task with ID 1
Scheduler> tasks fire 1  # Run task with ID 1 now, regardless of its schedule
```

### Automate Operations

```bash
# Run automation tasks locally or remotely
your_app automate run uname --verbose --as-json
```

### Receive Emails with the SMTPD Service

```bash
# Start the configured mail services (STARTTLS ready out of the box)
your_app smtpd serve
```

### Create Web Interfaces

```bash
# Start the web interface
your_app nicegui serve

# Access the interface at http://localhost:4123
```

### Ask an AI Agent (optional, requires tokeo-fundi)

```bash
# Available when the project was generated with the AI branch (tokeo-fundi)
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

### Control Debug Logging

```bash
# App debug logs
your_app --debug command

# Framework debug logs of cement, --debug does not enable them
CEMENT_LOG=1 your_app command
```

<br/>

## 🤖 Governed AI Agents with the Optional tokeo-fundi Extension

> The governed AI runtime described here is the separate [tokeo-fundi](https://github.com/tokeo/fundi) project, installed via ```tokeo-fundi```. Tokeo runs fully without it; add it when you want AI. See the [tokeo-fundi README](https://github.com/tokeo/fundi) for the complete picture.

[tokeo-fundi](https://github.com/tokeo/fundi) is a small AI agent runtime built on one rule: the model plans, the pipeline governs and the tools compute. Every step is explicit and can be inspected. It is compact, fully typed and tested end to end: the same suite drives the mock provider, the guard pipeline and a real trained model.

- Messages, tool calls, results and traces are typed values (```tokeo.core.ai```), independent of any provider SDK.
- An agent is a named guard chain in YAML. ```audited``` records everything and forbids nothing; ```guarded``` adds validation and policy (e.g. a readonly filesystem). With ```agent: null```, the default, requests run plain and untraced; governance is opted into.
- Tools are plain functions, registered with a spec and activated in groups (calendar, filesystem, mathematics) per profile. The provider never executes anything itself; results return as feedback through the guards.
- Providers are resolved late, by class path. The built-in ```mock``` provider makes the whole pipeline testable without any external service.

### akili

When you generate a project with the AI branch, it ships ```akili```, a train-first micro language model that plans calendar tool calls. It is small enough to read in an afternoon and proves the contracts end to end:

- A few hundred thousand parameters and a few megabytes, with a byte-level tokenizer and NumPy-only inference in tens of milliseconds, without GPU or service.
- No weights are shipped: ```python -m your_app.core.akili.train``` trains the model on your machine (a CPU is enough) and evaluates it on held-out data.
- Every word and sentence pattern lives in ```AKILI-LEX.yaml```. Teaching the model new language (English and German today) means editing that file and retraining. An ablation switch (```--no-minus```) shows live that the capability comes from the data while the code stays the same.
- A byte-level automaton constrains the planning, so the model cannot emit a malformed plan and only chooses among legal continuations.
- ```AKILI-LLM.md``` explains training, the anatomy of the weights and constrained decoding, with detailed diagrams.

### Providers

The same agents and guards run against every backend, so a change of provider is an edit of the profile and not a refactor:

- ```mock```: the deterministic local provider without server or network, the driver for tests and demos.
- ```akili```: the trained micro model, running in-process with plain NumPy.
- ```oai_compat```: any OpenAI-compatible endpoint you run yourself (Ollama, vLLM, llama.cpp, MLX) or a commercial API; the API key resolves through the vault, encrypted inside the config.

Response guards (redact, truncate), sandboxed tool execution (subprocess and a WebAssembly guest), and a code-mode tool that runs model-written Python behind that sandbox are all part of the runtime. The full description is in the [tokeo-fundi README](https://github.com/tokeo/fundi).

<br/>

## Developer Tools

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

## Deployment Options

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

## Project Structure

When you create a new project with Tokeo, you get a clean, modular structure:

- ```config/``` - Configuration files for prod, stage, dev and test environments
- ```your_app/core/logic``` - Space for your core application logic
- ```your_app/core/tasks/``` - Implementations of actors, agents, automations, operations, performers etc.
- ```your_app/core/ai/``` - AI providers and plain-function tools behind the guarded contracts (when the AI branch / tokeo-fundi is selected)
- ```your_app/core/akili/``` - The train-first micro LLM lab: model, lexicon (```AKILI-LEX.yaml```), teaching docs (when the AI branch is selected)
- ```your_app/core/grpc/``` - gRPC service definitions and implementations
- ```your_app/core/utils/``` - A place to put your overall tools and helper functions
- ```your_app/controllers/``` - Command-line interface controllers
- ```your_app/site/``` - Web interface pages and apis
- ```your_app/templates/``` - Templates for rendering content
- ```tests/``` - Test suite to ensure reliability

<br/>

## Next Steps

Tokeo is designed to grow with your project. Typical next steps in a new project:

- Customizing the application structure for your specific needs
- Creating new controllers for additional commands
- Adding task processors for background workloads
- Designing web interfaces to visualize data
- Adding the optional [tokeo-fundi](https://github.com/tokeo/fundi) runtime to wire your own AI provider behind the same guarded agent contracts
- Implementing automated deployment pipelines

<br/>
<br/>

## Tokeo packages

- Tokeo is the event-driven CLI framework: the command surface, messaging, scheduling, automation, gRPC, web, vault, and the project generator.
- Ramani ships all the project templates, including the AI branch and the akili lab that gets generated into your application.
- Fundi (```tokeo-fundi```) is the source-available AI runtime.
- Akili is generated by ```tokeo generate``` from Ramani's template and placed into your application for demonstration purposes under ```your_app/core/akili/``` when selected during your setup.

<br/>
<br/>

## ⚖️ Licensing Philosophy: Fair Use & Legal Certainty

I want the community to use Tokeo freely and the project to stay sustainable. That's why Tokeo and its extensions use two licenses.

### The Tokeo Core Framework (Apache 2.0)

I moved the core Tokeo framework from its original MIT license to the Apache 2.0 license. The switch was made for *you*, the implementer and the project, to give you legal certainty. The Apache 2.0 license also includes explicit patent grants and protection, a legally safe foundation for your projects.

### The Tokeo Fundi Agent AI Extension (Source-Available License)

The optional ```tokeo-fundi``` AI extension is governed by the custom Tokeo-Fundi Source-Available License 1.0. I want to keep software free for the community: if you are a hobbyist, a startup or a small team, ```tokeo-fundi``` is free of charge for you to use in any personal, internal or commercial project.

To keep this project healthy, actively developed, and sustainable, the license introduces a fair-use threshold. Businesses with highly successful commercial operations (across the whole group: more than EUR 2,000,000 in fiscal year global gross revenue excluding VAT, or more than 50 FTEs) are required to purchase a commercial Enterprise License. The license also restricts using its source code as training data for machine-learning systems. Read the complete and authoritative terms in ```LICENSE.md``` of the [tokeo-fundi](https://github.com/tokeo/fundi/) distribution.

Only installing ```tokeo-fundi```, also as a dependency of something else, is always free and carries no conditions. The limits apply only once you actually run it, through its commands or from your own code.

I believe this is a fair balance: those who achieve significant economic success using this software should contribute back to the developers making it possible.

### The Tokeo Akili Demonstration LLM (Apache 2.0)

```akili``` is not part of this package. It is generated into your application from Ramani's template and stays under the Apache License, Version 2.0. It is meant as a demonstration and is not intended for delivery. It ships so you can experiment and learn, and it can be removed without side effects.

<br/>
<br/>

## Support the Project

Tokeo is built in the open. If this approach is useful to you, a star on [GitHub](https://github.com/tokeo/tokeo) helps others find it. Issues, ideas, and pull requests are just as welcome.

<br/>
<br/>

## A note on contributions

We keep the human in the loop and use AI as an exoskeleton, the same conviction that shapes the runtime itself. Purely AI-generated issues or pull requests are not accepted.

Before you open a pull request, please read [CONTRIBUTING.md](CONTRIBUTING.md). It explains how contributions are licensed, what a change should bring with it, and what to run before you submit. Issues and ideas are just as welcome as code.

<br/>
<br/>

Check out the example project at [Tokeo Spiral](https://github.com/tokeo/spiral) and explore the [Makefile](https://github.com/tokeo/tokeo/blob/master/Makefile) and [extensions](https://github.com/tokeo/tokeo/tree/master/tokeo/ext) for more tools and customization options. Tokeo's modular design makes it easy to adapt for your backend needs.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
