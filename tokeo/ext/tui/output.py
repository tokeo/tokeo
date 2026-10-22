"""
Tokeo TUI Output Extension Module.

Tables and a live display for the terminal, available as ```app.tui.output```
once ```tokeo.ext.tui.output``` is enabled. Built on rich, which is loaded
when the extension is enabled.

### Features

- Tables with column alignment, cut to the terminal width
- A live display that repaints in place in a terminal and appends one block
    per repaint when the output goes to a pipe or a file
- Data is shown as given, brackets in it are never read as rich markup

### Example

```python
output = app.tui.output
table = output.table(['queue', ('ready', 'right')], [['mail', 3], ['orders', 0]])
output.print(table)

# repaint every 5 seconds until Ctrl-C
output.follow(lambda: output.table(['time'], [[time.strftime('%X')]]), interval=5)
```

"""

import io
import time
from cement.core.exc import CaughtSignal
from cement.core.foundation import SIGNALS
from tokeo.ext.tui import register, require


class TokeoTuiOutput:
    """
    Tables and live display for the terminal.

    ### Args

    - **app** (Application): The Cement application instance
    - **file** (file, optional): Where to write, the current stdout without
    - **width** (int, optional): The width, the terminal width without

    """

    def __init__(self, app, file=None, width=None):
        from rich.console import Console

        self.app = app
        # without a file rich writes to the current sys.stdout
        self._console = Console(file=file, width=width, highlight=False)

    @property
    def console(self):
        """The rich console, for output beyond the methods here."""
        return self._console

    @property
    def is_terminal(self):
        """Whether the output goes to a terminal."""
        return self._console.is_terminal

    def table(self, columns, rows, title=None):
        """
        Build a table.

        ### Args

        - **columns** (list): Column names, or tuples of name and alignment
            (```left```, ```center``` or ```right```)
        - **rows** (list): The rows, each a list of values; None shows empty
        - **title** (str, optional): A title above the table

        ### Returns

        - **rich.table.Table**: The table, to print or to combine

        """
        from rich import box
        from rich.table import Table

        table = Table(title=title, box=box.SIMPLE_HEAD, title_justify='left')
        for column in columns:
            name, justify = (column, 'left') if isinstance(column, str) else column
            table.add_column(name, justify=justify)
        for row in rows:
            table.add_row(*(_text(value) for value in row))
        return table

    def print(self, *renderables):
        """
        Print text, tables and other rich renderables, each on its own line.

        ### Args

        - **renderables**: What to print; plain strings are printed as they are

        """
        self._console.print(_group(renderables))

    def render(self, *renderables, width=None):
        """
        Render to plain text without colors or control characters.

        ### Args

        - **renderables**: What to render
        - **width** (int, optional): The width, the console width without

        ### Returns

        - **str**: The text

        """
        from rich.console import Console

        buffer = io.StringIO()
        Console(file=buffer, width=width or self._console.width, color_system=None, highlight=False).print(_group(renderables))
        return buffer.getvalue()

    def follow(self, render, interval, repaint=None, count=None):
        """
        Show what ```render``` returns again and again until Ctrl-C.

        ### Args

        - **render** (callable): Returns a renderable or a list of them
        - **interval** (float): Seconds between two repaints
        - **repaint** (bool, optional): Repaint in place, or append each
            block; without it repaints in a terminal only
        - **count** (int, optional): Stop after so many repaints

        ### Notes

        : Repainting uses the alternate screen of the terminal, so the screen
            shows its former content again after Ctrl-C

        : Ctrl-C and the signals the app shuts down on end the display, other
            caught signals pass

        """
        from rich.live import Live

        repaint = self.is_terminal if repaint is None else repaint
        shown = 0
        try:
            if repaint:
                with Live(console=self._console, screen=True, auto_refresh=False) as live:
                    while count is None or shown < count:
                        live.update(_group(_items(render())), refresh=True)
                        shown += 1
                        time.sleep(interval)
            else:
                while count is None or shown < count:
                    if shown:
                        self._console.print()
                    self._console.print(_group(_items(render())))
                    shown += 1
                    time.sleep(interval)
        except KeyboardInterrupt:
            pass
        except CaughtSignal as e:
            if e.signum not in SIGNALS:
                raise


def _text(value):
    """Turn a value into a renderable that shows it as it is."""
    from rich.protocol import is_renderable
    from rich.text import Text

    # a string would be read as rich markup, a Text is shown as it is
    if is_renderable(value) and not isinstance(value, str):
        return value
    return Text('' if value is None else str(value))


def _group(renderables):
    """Group renderables to show each on its own line."""
    from rich.console import Group

    return Group(*(_text(item) for item in renderables))


def _items(renderables):
    """Make a list of one or more renderables."""
    return list(renderables) if isinstance(renderables, (list, tuple)) else [renderables]


def load(app):
    """
    Load the tui output extension, it registers ```app.tui.output```.

    ### Args

    - **app** (Application): The Cement application instance

    ### Raises

    - **TokeoTuiError**: If the package rich is not installed

    """
    require('rich', 'rich', 'tokeo.ext.tui.output')
    register(app, 'output', TokeoTuiOutput(app))
