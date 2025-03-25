"""
OpenAPI documentation customization module.

This module provides customized OpenAPI schema generation and documentation
UI endpoints for the application. It configures both Swagger UI and ReDoc
interfaces with application-specific metadata including title, description,
version information, and branding.

The module handles an OpenAPI schema and provides endpoints to serve
both Swagger UI and ReDoc HTML documentation at '/_/api/docs' and
'/_/api/redoc' respectively.

### Notes:

- The OpenAPI schema is generated using FastAPI's built-in utilities
- Custom branding is applied including favicon integration
- The schema is cached in the FastAPI application instance
- Documentation endpoints are excluded from the schema itself

"""

from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.docs import get_redoc_html
from tokeo.ext.appshare import app
from {{ app_label }}.core.version import get_version


SUMMARY = """

The {{ app_name }} API Reference

Complete OpenAPI documentation for {{ app_name }} services and endpoints.

"""


DESCRIPTION = """
### About {{ app_name }} API

This API provides programmatic access to {{ app_name }} services and data.
The documentation below details all available endpoints, request parameters,
and response formats.

### Authentication (optional)

To use these API endpoints, you need to:

1. Generate API credentials from your dashboard
1. Include your API key with all requests

### Common Usage Patterns

* Retrieve data using GET requests
* Create resources using POST requests
* Update existing resources using PUT requests
* Remove resources using DELETE requests

### Rate Limits

API access may subject to rate limiting to ensure service stability.
Please refer to the response headers for rate limit information.

"""


def get_openapi_custom():
    """
    Generate and cache customized OpenAPI schema for the application.

    Creates the OpenAPI schema with application-specific metadata including
    title, description, version, contact info, and license details. The schema
    is cached in the FastAPI application instance for subsequent requests.

    ### Returns:

    - **dict**: Complete OpenAPI schema dictionary with all application routes
      and custom metadata

    ### Notes:

    : The function implements a caching mechanism - if the schema is already
      generated and stored in app.nicegui.fastapi_app.openapi_schema, it will
      return the cached version rather than regenerating it.

    """
    # check if already cached data exist
    if app.nicegui.fastapi_app.openapi_schema:
        # return the values
        return app.nicegui.fastapi_app.openapi_schema
    # prepare the openapi schema record
    openapi_schema = get_openapi(
        # informational settings
        title='{{ app_name }} OpenAPI Documentation',
        summary=SUMMARY,
        description=DESCRIPTION,
        version=f'{get_version()}',
        # Url to the terms-of-services document
        terms_of_service=None,
        # Contact dict
        contact=dict(
            name='{{ creator_name }}',
            url=None,
            email='{{ creator_email }}',
        ),
        # License dict
        license_info=dict(
            name='{{ project_license }}',
            identifier=None,
            url=None,
        ),
        routes=app.nicegui.fastapi_app.routes,
    )
    # change logo and images
    openapi_schema['info']['x-logo'] = dict(
        url='/favicon.ico',
    )
    # safe settings as cached data
    app.nicegui.fastapi_app.openapi_schema = openapi_schema
    # return the values
    return app.nicegui.fastapi_app.openapi_schema


@app.nicegui.fastapi_app.get('/_/api/docs', include_in_schema=False)
async def custom_swagger_ui_html():
    """
    Serve customized Swagger UI documentation page.

    Creates and returns a customized Swagger UI HTML page for interactive
    API documentation. The endpoint is mounted at '/_/api/docs' and uses
    the application's favicon.

    ### Returns:

    - **HTMLResponse**: Rendered Swagger UI HTML documentation page

    ### Notes:

    : This endpoint is excluded from the API schema itself via
      include_in_schema=False parameter to avoid recursive documentation.
      Further customization options are available in the FastAPI docs:
      https://fastapi.tiangolo.com/de/reference/openapi/docs/#fastapi.openapi.docs.get_swagger_ui_html

    """
    return get_swagger_ui_html(
        openapi_url=app.nicegui.fastapi_app.openapi_url,
        title=None,
        swagger_favicon_url='/favicon.ico',
    )


@app.nicegui.fastapi_app.get('/_/api/redoc', include_in_schema=False)
async def custom_redoc_html():
    """
    Serve customized ReDoc documentation page.

    Creates and returns a customized ReDoc HTML page for API documentation
    with an alternative UI to Swagger. The endpoint is mounted at '/_/api/redoc'
    and uses the application's favicon.

    ### Returns:

    - **HTMLResponse**: Rendered ReDoc HTML documentation page

    ### Notes:

    : This endpoint is excluded from the API schema itself via
      include_in_schema=False parameter to avoid recursive documentation.
      Further customization options are available in the FastAPI docs:
      https://fastapi.tiangolo.com/de/reference/openapi/docs/#fastapi.openapi.docs.get_redoc_html

    """
    return get_redoc_html(
        openapi_url=app.nicegui.fastapi_app.openapi_url,
        title=None,
        redoc_favicon_url='/favicon.ico',
    )
