"""
Operational and maintenance task functions for application management.

This module provides a centralized location for defining operator functions
that handle operational and maintenance tasks necessary to run and operate your
application built with Tokeo and Cement. Operators focus on system health,
data maintenance, monitoring, configuration management, and other operational
concerns rather than business logic.

### Features:

- **System maintenance** operations for database cleanup and optimization
- **Health checks** to monitor application components and dependencies
- **Data integrity** verification and repair procedures
- **Configuration management** for runtime application settings
- **Operational metrics** collection and reporting

### Usage:

Operators can be imported and used in various contexts throughout the application:

- **Actors**: For asynchronous scheduled maintenance tasks
- **Agents**: For timer-based system checks and monitoring
- **Automate**: For CLI-triggered operational procedures
- **Logic**: For actions based on business logic lifecycle events
- **Controllers**: For admin-facing operation commands

```python
from tokeo.ext.appshare import app

# In an actor
@app.dramatiq.actor(queue_name="maintenance")
def scheduled_maintenance():
    '''Run scheduled maintenance tasks.'''
    app.log.info("Starting scheduled maintenance")
    operators.purge_expired_data()
    operators.optimize_database()
    operators.verify_data_integrity()
```

### Notes:

- Operators should focus on operational concerns, not business logic
- Design operators to be idempotent when possible (safe to run multiple times)
- Include proper error handling and detailed logging
- Document failure modes and recovery procedures
- Operators should work in multiple contexts (CLI, scheduled jobs, etc.)
- Consider performance impacts when running maintenance operations
- Include safety checks to prevent accidental data loss

"""

from tokeo.ext.appshare import app  # noqa: F401
