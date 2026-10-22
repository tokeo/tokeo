"""
Tokeo TUI Extension Package.

General terminal input and output for Tokeo applications, split into two
extensions that are enabled one by one:

- ```tokeo.ext.tui.input``` adds ```app.tui.input```, an interactive shell
    with history, completion and suggestions, built on prompt_toolkit
- ```tokeo.ext.tui.output``` adds ```app.tui.output```, tables and a live
    display that repaints in place, built on rich

Each extension loads its library only when it is enabled, so an app adds just
the package it uses to its dependencies.

### Example

```python
class MyApp(App):
    class Meta:
        extensions = [
            'tokeo.ext.tui.input',
            'tokeo.ext.tui.output',
        ]

# a table of the current state
table = app.tui.output.table(['worker', ('done', 'right')], [['w-01', 102]])
app.tui.output.print(table)

# a shell that runs each entered line
app.tui.input.shell(lambda line: run(line), prompt='MyApp> ')
```

"""

import importlib
from tokeo.core.exc import TokeoError


class TokeoTuiError(TokeoError):
    """Errors of the tui extensions."""


class TokeoTui:
    """
    The tui parts of an app, available as ```app.tui```.

    ### Notes

    : Accessing a part whose extension is not enabled raises a
        ```TokeoTuiError``` that names the extension to enable

    """

    def __init__(self):
        self._parts = {}

    def _part(self, name):
        if name not in self._parts:
            raise TokeoTuiError(f'Enable the extension tokeo.ext.tui.{name} to use app.tui.{name}!')
        return self._parts[name]

    @property
    def input(self):
        """The interactive shell, see ```TokeoTuiInput```."""
        return self._part('input')

    @property
    def output(self):
        """Tables and live display, see ```TokeoTuiOutput```."""
        return self._part('output')


def register(app, name, part):
    """
    Register a tui part as ```app.tui.<name>```.

    ### Args

    - **app** (Application): The Cement application instance
    - **name** (str): The part, ```input``` or ```output```
    - **part** (object): The instance to register

    """
    # the first enabled tui extension adds app.tui
    if not hasattr(app, 'tui'):
        app.extend('tui', TokeoTui())
    app.tui._parts[name] = part


def require_extensions(app, extension, *needed):
    """
    Check that the tui extensions an extension builds on are enabled.

    ### Args

    - **app** (Application): The Cement application instance
    - **extension** (str): The extension that needs them
    - **needed** (str): The extensions it needs, e.g. ```tokeo.ext.tui.output```

    ### Raises

    - **TokeoTuiError**: If one of them is not enabled

    """
    missing = [name for name in needed if name not in app.ext.get_loaded_extensions()]
    if missing:
        names, them = ' and '.join(missing), 'it' if len(missing) == 1 else 'them'
        raise TokeoTuiError(f'The extension {extension} needs {names}, add {them} to the extensions of the app!')


def require(module, package, extension):
    """
    Import the library of a tui extension.

    ### Args

    - **module** (str): The module to import, e.g. ```rich```
    - **package** (str): The package to install for it
    - **extension** (str): The extension that needs it

    ### Raises

    - **TokeoTuiError**: If the library is not installed

    """
    try:
        importlib.import_module(module)
    except ImportError as e:
        raise TokeoTuiError(f'The extension {extension} needs the package {package}, add it to the app dependencies ({e})!') from None
