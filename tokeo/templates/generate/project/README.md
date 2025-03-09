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

- **AI Integration**: Add intelligence with machine learning using scikit-learn, PyTorch, or integrate with LLMs
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

## 📚 Project Structure

Your project is organized into a clean, modular structure:

- `config/` - Configuration files for prod, stage, dev and test environments
- `{{ app_label }}/controllers/` - Command-line interface controllers
- `{{ app_label }}/core/logic` - Space for your core application logic
{% if feature_grpc == "Y" %}
- `{{ app_label }}/core/grpc/` - gRPC service definitions and implementations
{% endif %}
{% if feature_nicegui == "Y" %}
- `{{ app_label }}/core/pages/` - Web interface pages and apis
{% endif %}
- `{{ app_label }}/core/tasks/` - All implementations of actors, agents, automation, operations, performers and others
- `{{ app_label }}/core/utils/` - A place to put your overall tools and helper functions
- `{{ app_label }}/templates/` - Templates for rendering content
- `tests/` - Test suite to ensure reliability

<br/>

## 🌟 Making It Your Own

This is just the beginning of your journey. As you build and shape this project, consider:

- What problem are you trying to solve?
- Who will use your application and how?
- How can you make it not just functional, but delightful to use?

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

<p>
  Built with ❤️ and <a href="https://github.com/tokeo/tokeo">tokeo</a> - Empowering Python Applications
</p>
