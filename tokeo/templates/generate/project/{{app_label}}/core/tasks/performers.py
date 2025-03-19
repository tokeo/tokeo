"""
Business task performers for orchestrating application workflows.

This module provides a centralized location for defining performer functions
that orchestrate complex business workflows in Tokeo and Cement applications.
Performers act as intermediaries between controllers and asynchronous workers,
handling database interactions, business logic, and initiating asynchronous
processing via dramatiq actors.

### Features:

- **Controller-invoked workflows** for handling business requests
- **Database interaction** with PocketBase collections
- **Business logic orchestration** connecting different application components
- **Asynchronous processing** through dramatiq actors
- **Reusable business operations** for consistent implementation

### Usage:

Define performer functions that orchestrate complex operations:

```python
from tokeo.ext.appshare import app
from myapp.core.tasks import actors

def process_order(order_id, notify_customer=True):
    '''
    Process a customer order through the complete business workflow.

    This performer function orchestrates the order processing workflow,
    including database operations, validation, and initiating async tasks.

    '''
    # Retrieve order data from database
    order = app.db.get_one('orders', order_id)

    # Validate order status
    if order.status != 'pending':
        app.log.warning(
            f"Cannot process order {order_id} with status {order.status}"
        )
        return {'success': False, 'reason': f"Invalid order status: {order.status}"}

    # Update order status in database
    app.db.update('orders', order_id, {'status': 'processing'})

    # Trigger async processing via dramatiq actor
    actors.fulfill_order.send(order_id)

    # Optionally notify customer
    if notify_customer:
        actors.send_order_status_notification.send(order_id, 'processing')

    return {'success': True, 'order_id': order_id}
```

Invoke performers from controllers:

```python
@ex(help='Process a pending order')
def process(self):
    order_id = self.app.pargs.id
    result = self.app.{{app_label}}.performers.process_order(order_id)
    if result['success']:
        self.app.log.info(f"Order {order_id} processing initiated")
    else:
        self.app.log.error(f"Order processing failed: {result['reason']}")
```

### Notes:

- Performers act as the bridge between synchronous requests and asynchronous
  processing
- Use performers to orchestrate complex workflows involving multiple steps
- Performers typically interact with the database, perform business logic,
  and queue async tasks
- Centralize business rules in performers to maintain consistency across
  the application
- Controllers invoke performers, which in turn may invoke dramatiq actors
  for async work

"""

from tokeo.ext.appshare import app  # noqa: F401
