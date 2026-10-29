The page decorator marks this function to be a page builder.

Each user accessing the given route will see a new instance of the page.
This means it is private to the user and not shared with others (as it is done when
placing elements outside of a page decorator).

### Args

- **path** (str): Route of the new page, it must start with ```/```
- **title** (str, optional): Page title
- **viewport** (str, optional): Content of the viewport meta tag
- **favicon** (str|Path, optional): Path to a local image file, an absolute
    URL, a data URL, an inline SVG or an emoji. Defaults to None, the favicon
    of ```ui.run``` is used.
- **dark** (bool, optional): Whether to use Quasar's dark mode. Defaults to the
    ```dark``` argument of the run command.
- **language** (str, optional): Language of the page. Defaults to the
    ```language``` argument of the run command.
- **response_timeout** (float, optional): Maximum time in seconds for the
    decorated function to build the page. Defaults to 3.0.
- **reconnect_timeout** (float, optional): Maximum time in seconds the server
    waits for the browser to reconnect. Defaults to the ```reconnect_timeout```
    argument of the run command.
- **markdown** (bool, optional): Whether to serve a Markdown representation
    when a client sends ```Accept: text/markdown```, experimental. Defaults to
    the ```markdown``` argument of the run command.
- **api_router** (APIRouter, optional): The router of the page. Defaults to
    None, the default router is used.
- **kwargs**: Additional keyword arguments passed to the ```@app.get``` method
    of FastAPI

### Notes

: The name of the decorated function is unused and can be anything.

: The page route is determined by the path argument and registered globally.

: The decorator works only for free functions and static methods. Instance
    methods or initializers would require a self argument, which the router
    cannot associate. See the nicegui modularization example for strategies to
    structure your code.
