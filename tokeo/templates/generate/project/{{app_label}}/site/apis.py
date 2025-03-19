"""
REST API endpoints and business logic integration for the application.

This module provides a centralized location for defining API endpoints and
integrating business logic in Tokeo and Cement applications using NiceGUI's
FastAPI integration. It contains REST API routes that expose application
functionality to clients, connecting web interfaces with the core business
logic implemented in the application's core modules.

### Features:

- **REST API endpoints** for client-server communication
- **Business logic integration** with core.tasks modules
- **JSON data exchange** for frontend/backend interaction
- **Structured request/response handling** with FastAPI
- **API documentation** through automatically generated OpenAPI specs

### API Structure:

API endpoints are defined as functions decorated with FastAPI decorators
like `@app.nicegui.fastapi_app.get`, `@app.nicegui.fastapi_app.post`, etc.
These endpoints connect web interfaces with the application's business logic
by invoking appropriate functions from the core.tasks modules (performers,
operators, steps).

### Usage:

Define a new API endpoint using FastAPI decorators:

```python
from tokeo.ext.appshare import app
from myapp.core.tasks import performers

@app.nicegui.fastapi_app.post('/api/products/search')
async def search_products(request: ProductRequest):
    '''
    Search for products by category with optional limit.

    This endpoint allows searching for products in a specific category,
    with pagination through the limit parameter.

    '''
    # Use performers module to access business logic
    result = performers.search_products(
        category_id=request.category_id,
        limit=request.limit
    )

    # Transform business data to API response format
    products = [
        dict(
            id=product['id'],
            name=product['name'],
            price=product['price'],
            description=product.get('description'),
        )
        for product in result['products']
    ]

    return products
```

### Integration Points:

The APIs module can interact with various parts of the application:

- **performers**: For complex business workflows and orchestration
- **operators**: For maintenance and operational tasks
- **steps**: For atomic business operations and utilities
- **models**: For data access and persistence
- **services**: For external service integration

### Notes:

- API endpoints should follow REST conventions and best practices
- Use FastAPI's typing system for request/response validation
- Implement proper error handling and status codes
- Keep business logic in core.tasks modules, not in API handlers
- Consider authentication and authorization requirements
- API endpoints are automatically documented via OpenAPI/Swagger UI

"""

from tokeo.ext.appshare import app


@app.nicegui.fastapi_app.get('/api')
async def get_api():
    """
    Basic API endpoint demonstrating the structure.

    This endpoint provides a simple example of how to define
    API routes in the application. It returns a basic message
    to verify the API is functioning.

    ### Returns:

    - JSON response with a message field

    ### Example Response:

    ```json
    {
        "msg": "myapp json api result"
    }
    ```
    """
    return dict(msg='{{ app_label }} json api result')
