"""
Business task agents fired by the Tokeo scheduler.

This module provides a centralized location for defining scheduling agent functions
that are triggered by timers in Tokeo and Cement applications. Agents act as entry
points for timed operations, often delegating actual processing to asynchronous
workers for complex tasks.

### Features:

- **Scheduled task execution** using cron-style expressions
- **Timer-based operations** with configurable jitter and delay
- **Centralized agent definitions** for maintainable code organization
- **Integration with application context** via app-specific business logic
- **Delegation to asynchronous workers** for complex processing

### Usage:

Define timer agents as simple Python functions:

```python
from tokeo.ext.appshare import app

def process_daily_report(report_type='summary'):
    '''
    Agent function triggered by daily scheduler timer.

    Initiates generation of daily reports by delegating to an async worker.
    '''
    app.log.info(f'Starting daily {report_type} report generation')
    # Delegate actual processing to an asynchronous worker
    app.dramatiq.actors.generate_report.send(report_type)
```

Configure scheduled execution in your application's config file:

```yaml
scheduler:
  timezone: UTC
  tasks:
    process_daily_report:
      module: myapp.core.tasks.agents
      name: Daily Report Generator
      coalesce: latest
      crontab: '0 1 * * *'  # Run at 1:00 AM daily
      kwargs:
        report_type: 'detailed'
```

### Notes:

- Agent functions are fired by the scheduler based on their cron expressions
- For complex or long-running tasks, agents should delegate to async workers
- Each agent function should be configured in the application's config file
- Agents have access to the application context via the app (appshare) object

"""

from tokeo.ext.appshare import app  # noqa: F401
from {{ app_label }}.core import tasks


def count_words_timer(url=''):
    """
    Agent function triggered by a scheduler timer to: 'count words on a webpage'.

    This example agent delegates the actual word counting to an asynchronous
    worker using Dramatiq.

    ### Args:

    - **url** (str, optional): URL of the webpage to analyze.
      Argument is defined in config files.

    """
    app.log.info(f'Timer start with url: {url}')
    tasks.actors.count_words.send(url)
