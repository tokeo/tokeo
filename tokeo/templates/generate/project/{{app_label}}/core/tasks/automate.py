"""
Business automation functions for CLI and remote execution in Tokeo applications.

This module provides a centralized location for defining business automation functions
that can be executed via CLI commands in Tokeo and Cement applications. Automation
functions typically handle local or remote task execution and are primarily
triggered by direct CLI invocation.

### Features:

- **Command-line automation** functions with standardized interfaces
- **Local and remote execution** via SSH or local shells
- **Host-based configuration** for consistent deployment across environments
- **Integration with application context** via the app parameter
- **Bridging to asynchronous processing** by triggering dramatiq actors

### Usage:

Define automation functions with the standard interface pattern:

```python
from tokeo.ext.appshare import app

def process_report(app, connection, verbose=False, report_type='summary'):
    '''
    Generate a business report on the target system.

    This automation function connects to the target system and executes
    report generation commands, capturing and returning the results.

    ### Args:

    - **app** (Application): Application context with configuration and logging
    - **connection** (Connection): SSH or local shell connection to execute commands
    - **verbose** (bool, optional): Whether to show command output. Defaults to False.
    - **report_type** (str, optional): Type of report to generate. Defaults to 'summary'.

    ### Returns:

    - **Result**: Command execution result with stdout/stderr and exit code

    '''
    app.log.info(f'Generating {report_type} report')
    result = connection.run(
        f'generate-report --type {report_type}',
        hide=not verbose,
        warn=False,
    )
    return result
```

Configure the automation in your application's config file:

```yaml
automate:
  tasks:
    process_report:
      module: myapp.core.tasks.automate
      name: Report Generator
      hosts: reporting_server
      kwargs:
        report_type: 'detailed'
```

Execute the automation via CLI:

```bash
myapp automate run process_report
```

### Notes:

- All automation functions must accept the app and connection parameters
- Connection provides run() for executing commands on the target system
- Use the verbose parameter to control command output visibility
- For long-running tasks, consider delegating to dramatiq actors
- Return values are typically command execution results

"""

from {{ app_label }}.core import tasks


def count_words(app, connection, verbose=False):
    """
    Count words on a web page after gathering system information.

    This automation example executes a system command on the target,
    creates a search URL with the result, and delegates word counting
    to an asynchronous actor.

    ### Args:

    - **app** (Application): Application context with configuration and logging
    - **connection** (Connection): SSH or local shell connection to execute commands
    - **verbose** (bool, optional): Whether to show command output. Defaults to False.

    ### Returns:

    - **bool**: True if the automation completed successfully

    """
    app.log.info('Automation count_words called')
    result = connection.run('uname -mrs', hide=not verbose, warn=False)
    url = f'https://google.com/q="{result.stdout}"'.replace('\n', '').replace('\r', '')
    app.log.info(f'Run actor from automation with url: {url}')
    tasks.actors.count_words.send(url)
    return True


def uname(app, connection, verbose=False, flags=None):
    """
    Execute the uname command on a target system with specified flags.

    This automation function demonstrates running a simple system command
    with configurable parameters on the target system.

    ### Args:

    - **app** (Application): Application context with configuration and logging
    - **connection** (Connection): SSH or local shell connection to execute commands
    - **verbose** (bool, optional): Whether to show command output. Defaults to False.
    - **flags** (list, optional): List of flags to pass to the uname command

    ### Returns:

    - **Result**: Command execution result with stdout/stderr and exit code

    """
    app.log.info('Automation uname called')
    return connection.run(f'uname {" ".join(flags)}', hide=not verbose, warn=False)
