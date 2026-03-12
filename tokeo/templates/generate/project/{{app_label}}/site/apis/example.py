"""
REST API endpoints and business logic integration for the application.

This module provides isolated API functions for Tokeo applications using
FastAPI integration. It contains REST API routes that expose application
functionality to clients, connecting web interfaces with core business logic.

### Features:

- **Isolated endpoint functions**: Prevents global state pollution
- **Business logic integration**: Seamless connection with `core.tasks`
- **JSON data exchange**: For frontend/backend interaction
- **API documentation** through automatically generated OpenAPI specs

### API Structure:

Endpoints are defined as pure asynchronous functions. Instead of using
decorators directly in this file, these functions are imported and mapped
programmatically inside `site/routes.py`.

### Usage:

Define a new API endpoint as a pure function:

##### create `site/apis/products.py`

```python
from myapp.core.tasks import performers

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

##### register it in `site/routes.py`:

```python
def apis_map():
    fa.post('/_/api/products/search')(search_products)
```

### Integration Points:

The APIs module can interact with various parts of the application:

- **performers**: For complex business workflows and orchestration
- **operators**: For maintenance and operational tasks
- **steps**: For atomic business operations and utilities
- **models**: For data access and persistence
- **services**: For external service integration

### Notes:

- Keep business logic in `core.*` modules, not in the API handlers
- Use FastAPI's typing system for request/response validation
- Implement proper error handling and status codes
- Consider authentication and authorization requirements
- Endpoints are automatically documented via OpenAPI/Swagger UI

"""


async def api_example():
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
