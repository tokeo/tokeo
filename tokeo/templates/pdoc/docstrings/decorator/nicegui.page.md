The page decorator marks this function to be a page builder.

Each user accessing the given route will see a new instance of the page.
This means it is private to the user and not shared with others (as it is done when placing elements outside of a page decorator).

### Notes:

- The name of the decorated function is unused and can be anything.
- The page route is determined by the path argument and registered globally.
- The decorator does only work for free functions and static methods.
    Instance methods or initializers would require a self argument,
    which the router cannot associate. See the nicegui modularization
    example for strategies to structure your code.

### Page Configuration:

- **path** (str):	route of the new page (path must start with '/')
- **title** (str):	optional page title
- **viewport** (str):	optional viewport meta tag content
- **favicon** (str):	optional relative filepath or absolute URL to a favicon (default: None, NiceGUI icon will be used)
- **dark** (bool):	whether to use Quasar's dark mode (defaults to dark argument of run command)
- **language** (str):	language of the page (defaults to language argument of run command)
- **response_timeout** (int):
    maximum time for the decorated function to build the page (default: 3.0 seconds)
- **reconnect_timeout** (int):
    maximum time the server waits for the browser to reconnect (defaults to reconnect_timeout argument of run command))
- **kwargs**:	additional keyword arguments passed to FastAPI's @app.get method
