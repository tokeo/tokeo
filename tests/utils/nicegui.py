"""
Run an app of the tests with nicegui in its user simulation.

### Example

```python
from tests.utils.nicegui import NiceguiSimulation
```

"""

import os
import asyncio
import httpx
from nicegui import core, ui
from nicegui.testing import User
from nicegui.testing.general import nicegui_reset_globals


class NiceguiSimulation:
    """
    Run a test app with the nicegui extension in nicegui's user simulation.

    The app starts by ```app.nicegui.startup()``` as it does when served,
    nicegui answers an ASGI client without a browser, and ```visit``` drives
    the pages by the ```User``` of ```nicegui.testing```. The global state
    of nicegui is reset before and after; a failure of ```visit``` is raised
    once nicegui shut down, so the next test always starts clean.

    ### Args

    - **app** (App): A test app with the nicegui extension, inside its with

    ### Example

    ```python
    def test_page_shows_label():
        with NiceguiTest(routes='tests.ext.nicegui.test_nicegui:_pages') as app:

            async def visit(simulation):
                user = simulation.user()
                await user.open('/')
                await user.should_see('Hello tokeo')
                # an api of the app by the ASGI client
                assert (await simulation.client.get('/api/ping')).status_code == 200

            NiceguiSimulation(app).run(visit)
    ```

    ### Notes

    : The simulation patches ```ui.navigate```, ```ui.notify``` and
        ```ui.download``` and sets ```NICEGUI_USER_SIMULATION```; both are
        restored after ```run()```

    """

    #: The functions of ui the User of nicegui replaces
    PATCHED = ('navigate', 'notify', 'download')

    def __init__(self, app):
        #: The test app
        self.app = app
        #: (httpx.AsyncClient) The client of the running app, set while running
        self.client = None

    def user(self):
        """A new visitor of the running app, a ```User``` of nicegui."""
        return User(self.client)

    def run(self, visit):
        """
        Start the app, await ```visit(simulation)``` and shut nicegui down.

        ### Args

        - **visit** (coroutine function): Called with the simulation while
            the app runs

        ### Raises

        - **BaseException**: What visit raised, once nicegui shut down

        """
        failures = []

        async def go():
            self.app.nicegui.startup()
            async with core.app.router.lifespan_context(core.app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(core.app), base_url='http://test') as client:
                    self.client = client
                    try:
                        await visit(self)
                    except BaseException as err:
                        failures.append(err)
                    finally:
                        self.client = None

        environ = os.environ.get('NICEGUI_USER_SIMULATION')
        functions = {name: getattr(ui, name) for name in self.PATCHED}
        os.environ['NICEGUI_USER_SIMULATION'] = 'true'
        try:
            with nicegui_reset_globals():
                asyncio.run(go())
            # routes, handlers and clients of this run do not reach the next one
            with nicegui_reset_globals():
                pass
        finally:
            for name, function in functions.items():
                setattr(ui, name, function)
            if environ is None:
                os.environ.pop('NICEGUI_USER_SIMULATION', None)
            else:
                os.environ['NICEGUI_USER_SIMULATION'] = environ
        if failures:
            raise failures[0]
