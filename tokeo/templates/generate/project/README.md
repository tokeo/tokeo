<h1 align="center">{{ app_name }}</h1>

<p align="center">
  <strong>{{ app_description }}</strong>
</p>
<p align="center">
  Created with 💪 by {{ creator_name }}
</p>

<br/>

## 🚀 Welcome to Your New Journey

Congratulations on creating your **{{ app_name }}** project! This is more than just code – it's the foundation for bringing your ideas to life. Whether you're building a data analysis tool, a web service, or an AI-powered application, you've taken the first step toward creating something meaningful.

> "The best way to predict the future is to invent it." – Alan Kay

### 🎯 What's Next?

Your application is ready for you to explore and expand. Here are some exciting directions you might take:

{% if feature_ai == "Y" %}
- **Agentic AI**: Built in and governed, ask via `{{ app_label }} ai ask`, every tool call passes validate, policy and audit.{% if feature_ai_akili == "Y" %} Trained own micro model in `core/akili`.
{% endif %}
{% else %}
- **AI Integration**: Add intelligence by integrating LLMs or classic ML pipelines
{% endif %}
- **Data Exploration**: Uncover insights by analyzing data with pandas, matplotlib, or seaborn
- **Web Interfaces**: Create beautiful dashboard and web tools with the built-in NiceGUI extension and tailwindcss based admin theme
- **Automation**: Schedule tasks and create workflows with the scheduler extension or total local and remote automation
- **API Development**: Build robust APIs for your services

Remember, every great application started exactly where you are now!

<br/>

## 🛠️ Getting Started

### Installation

First, set up your virtual environment:

```bash
# Create and activate virtual environment
make venv
source .venv/bin/activate

# Install development dependencies
make dev
```

### Running Your Application

Once installed, you can launch your application:

```bash
# See available commands
{{ app_label }} --help

# Run a specific command
{{ app_label }} <command>
```

{% if feature_grpc == "Y" %}
### Compiling Protocol Buffers

If you're using gRPC services, you have to run:

```bash
# Generate Python code from proto files
make proto
```
{% endif %}

<br/>

## 📊 Exploring Tokeo Features

{% if feature_dramatiq == "Y" %}
### Process Background Tasks with Dramatiq (needs a running RabbitMQ)

```bash
# Launch Dramatiq workers to process background tasks
{{ app_label }} dramatiq serve

# Trigger a task (e.g., count-words)
{{ app_label }} emit count-words --url https://github.com
```

{% endif %}
{% if feature_grpc == "Y" %}
### Expose Services via gRPC

```bash
# Start the gRPC server for external task access
{{ app_label }} grpc serve

# Execute a task using the gRPC client
{{ app_label }} grpc-client count-words --url https://github.com
```

{% endif %}
{% if feature_apscheduler == "Y" %}
### Schedule Recurring Tasks

```bash
# Run the scheduler with interactive shell
{{ app_label }} scheduler launch

# Within the scheduler shell, list and manage tasks
Scheduler> list
Scheduler> tasks pause 1 2 3  # Pause task with ID 1, 2, 3
Scheduler> tasks resume 1  # Resume task with ID 1
Scheduler> tasks fire 1  # Resume task with ID 1
```

{% endif %}
{% if feature_automate == "Y" %}
### Automate Operations

```bash
# Run automation tasks locally or remotely
{{ app_label }} automate run uname --verbose --as-json
```

{% endif %}
{% if feature_nicegui == "Y" %}
### Create Web Interfaces

```bash
# Start the web interface
{{ app_label }} nicegui serve

# Access the interface at http://localhost:4123
```

{% endif %}
{% if feature_diskcache == "Y" %}
### Use Diskcache

```bash
# List content
{{ app_label }} cache list

# Set value
{{ app_label }} cache set counter --value 1 --value-type int

# Get value
{{ app_label }} cache get counter
```

{% endif %}
{% if feature_ai == "Y" %}
### Ask an AI Agent

Your application speaks to AI providers through one governed runtime -- **the model plans, the pipeline governs, the tools compute**. Profiles and agents are plain YAML in `config/`: `audited` records everything and forbids nothing, `guarded` adds validation and policy. The tools are your own plain functions in `{{ app_label }}/core/ai/tools/`, activated in groups per profile.

```bash
# The mock provider answers without any external service
{{ app_label }} ai ask "ping"

# Inspect agents, profiles, and registered tools
{{ app_label }} ai list
```
{% if feature_ai_akili == "Y" %}

Your project also ships **akili**, a train-first micro LLM (378,240 parameters, ~1.5 MB) that plans calendar tool calls. No weights are included -- you create them, and that is the point:

```bash
# Train on your machine (CPU is fine)
python -m {{ app_label }}.core.akili.train

# Then ask, in English or German -- guarded, traced, deterministic
{{ app_label }} ai ask "the weekday of today plus 2 days" --profile akili --agent guarded
{{ app_label }} ai ask "welches datum ist übermorgen" --profile akili
```

The model's whole language lives in `{{ app_label }}/core/akili/AKILI-LEX.yaml`: teach it new words and sentence patterns by editing the file and retraining. `AKILI-LLM.md` next to it explains training, the anatomy of the weights, and grammar-constrained decoding with detailed diagrams.
{% endif %}

{% endif %}

<br/>

## 📊 Development Tools

This project includes several helpful commands to streamline your development:

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

## Control Logging

Set log levels via config, environment variables, or CLI flags:

```bash
# App debug logs
{{ app_label | upper }}_LOG_COLORLOG_LEVEL=debug {{ app_label }} command

# App + framework debug logs
{{ app_label }} --debug command

# Framework debug logs only
CEMENT_LOG=1 {{ app_label }} command
```

<br/>

## 📚 Project Structure

Your project is organized into a clean, modular structure:

- `config/` - Configuration files for prod, stage, dev and test environments
- `{{ app_label }}/core/logic` - Space for your core application logic
- `{{ app_label }}/core/tasks/` - Implementations of actors, agents, automations, operations, performers etc.
{% if feature_ai == "Y" %}
- `{{ app_label }}/core/ai/` - Your AI providers and plain-function tools behind the guarded contracts
{% endif %}
{% if feature_ai == "Y" and feature_ai_akili == "Y" %}
- `{{ app_label }}/core/akili/` - The train-first micro LLM lab: model, lexicon (`AKILI-LEX.yaml`), teaching docs
{% endif %}
{% if feature_grpc == "Y" %}
- `{{ app_label }}/core/grpc/` - gRPC service definitions and implementations
{% endif %}
- `{{ app_label }}/core/utils/` - A place to put your overall tools and helper functions
- `{{ app_label }}/controllers/` - Command-line interface controllers
{% if feature_nicegui == "Y" %}
- `{{ app_label }}/site/` - Web interface pages and apis
{% endif %}
- `{{ app_label }}/templates/` - Templates for rendering content
- `tests/` - Test suite to ensure reliability

<br/>

## 🌟 Making It Your Own

This is just the beginning of your journey. As you build and shape this project, consider:

- What problem are you trying to solve?
- Who will use your application and how?
- How can you make it not just functional, but delightful to use?
{% if feature_ai == "Y" %}
- Which routine work could a governed agent take over and which tools would you trust it with?
{% endif %}

<br/>

## 🔄 Continuous Improvement

Keep your project healthy with these practices:

- Document your code and add examples
- Write tests for new features
- Refactor when needed for clarity
- Stay up-to-date with your packages using `make outdated`

<br/>

## 🤝 Need Help?

If you encounter challenges or have questions:

- Check the Tokeo documentation
- Explore similar open-source projects for inspiration
- Connect with the community of developers

<br/>
<br/>

Built with ❤️ and <a href="https://github.com/tokeo/tokeo">tokeo</a> - Empowering Python Applications
