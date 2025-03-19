"""
Tokeo Print Extension Module.

This extension provides enhanced printing capabilities for Tokeo applications.
It integrates with the Cement framework to provide structured output handling
for print operations, ensuring that pre_render and post_render hooks are honored.

The extension provides framework-aware printing functionality that respects the
application's output handling pipeline, including hooks, formatters, and
rendering templates.

### Features:

1. **app.print()** - A framework-aware replacement for Python's built-in print()
1. **app.inspect()** - A detailed inspection utility for examining objects
1. **Output handlers** - Specialized handlers for different rendering styles:
    - Print - Basic text output with hooks
    - Print Dict - Key-value pair rendering for dictionaries
    - Inspect - Detailed object introspection with type information

### Example:

```python
# Basic printing
app.print("Hello, world!")

# Print with a divider and custom name
app.print("Section start", divider="-", name="section-header")

# Detailed object inspection
user = {'name': 'John', 'age': 30}
app.inspect(user, methods=True, attributes=True)

# Inspect multiple objects with type information
app.inspect(user, "string value", 42, types=True)

# Debug output
app.inspect(complex_object, debug=True)
```

"""

from __future__ import annotations
from typing import Any, Dict, Union, TYPE_CHECKING
from cement.core import output

if TYPE_CHECKING:
    from cement.core.foundation import App  # pragma: nocover

import inspect


def register_tokeo_print(app: App) -> None:
    """
    Register print and inspect functionality with the application.

    This function extends the application with print and inspect methods
    that use the appropriate output handlers, integrating with the
    Cement framework's rendering pipeline.

    ### Args:

    - **app**: The application object to extend

    ### Notes:

    : The registered methods become available as app.print() and app.inspect()
      throughout the application lifetime. These methods provide framework-aware
      alternatives to Python's built-in printing and inspection capabilities.

    """

    def _print(*args: any, name=None, sep=' ', end='\n', divider=None) -> None:
        """
        Print output using the application's print handler.

        Framework-aware alternative to Python's built-in print() function
        that respects the application's rendering pipeline and hooks.

        ### Args:

        - ***args**: Variable arguments to print
        - **name** (str, optional): Name to identify this print operation in logs
        - **sep** (str): Separator between printed items (default: space)
        - **end** (str): String to append at the end (default: newline)
        - **divider** (str, optional): Character to use for divider line
          (prints 40 of these)

        """
        app.render(dict(args=args, name=name, sep=sep, end=end, divider=divider), handler='print')

    app.extend('print', _print)

    def _inspect(
        *args: any,
        name=None,
        system=False,
        methods=False,
        attributes=False,
        values=True,
        types=True,
        debug=False,
        divider=None,
    ) -> None:
        """
        Inspect objects and print their details.

        Provides detailed introspection of objects including values, types,
        methods, and attributes. Useful for debugging and exploring objects.

        ### Args:

        - ***args**: Objects to inspect
        - **name** (str, optional): Name to identify this inspection in output
        - **system** (bool): Whether to include system methods/attributes
          (those with __name__)
        - **methods** (bool): Whether to display object methods
        - **attributes** (bool): Whether to display object attributes
        - **values** (bool): Whether to display object values
        - **types** (bool): Whether to display object types
        - **debug** (bool): Whether to output to debug log instead of stdout
        - **divider** (str, optional): Character to use for divider line
          (prints 40 of these)

        """
        app.render(
            dict(
                args=args,
                name=name,
                system=system,
                methods=methods,
                attributes=attributes,
                values=values,
                types=types,
                debug=debug,
                divider=divider,
            ),
            handler='inspect',
        )

    app.extend('inspect', _inspect)


class TokeoPrintOutputHandler(output.OutputHandler):
    """
    Output handler for print operations.

    This class implements the Output Handler interface for simple printing.
    It takes a dict and renders the contained arguments similar to the
    built-in print() function, but with framework features like pre_render
    and post_render hooks.

    ### Methods:

    - **render**: Formats and returns the text output
    - **_print**: Internal method to format arguments for printing

    ### Notes:

    : This handler is registered with the label 'print' and is used by the
      app.print() method. It formats the provided arguments with the
      specified separator and end strings, similar to Python's built-in
      print() function, but integrates with the application's rendering
      pipeline for consistent output handling.

    """

    class Meta(output.OutputHandler.Meta):
        """
        Handler meta-data configuration.

        ### Notes:

        : This class defines the metadata required by the Cement framework for
          proper handler registration and operation. It specifies how the
          output handler is identified and whether it can be overridden via
          command line options.

        """

        label = 'print'
        """The string identifier of this handler."""

        #: Whether or not to include ``print`` as an available choice
        #: to override the ``output_handler`` via command line options.
        overridable = False

    _meta: Meta  # type: ignore

    def _print(self, args, sep=' ', end='\n', divider=None):
        """
        Format arguments for printing.

        Internal method that formats the provided arguments into a string
        ready for output, similar to Python's built-in print() function.

        ### Args:

        - **args** (tuple): The arguments to print
        - **sep** (str): Separator between printed items
        - **end** (str): String to append at the end
        - **divider** (str, optional): Character to use for divider line

        ### Returns:

        - **str**: The formatted string ready for output

        ### Notes:

        : This method joins the string representations of all provided arguments
          using the specified separator, adds the end string, and optionally
          prepends a divider line made of 40 repetitions of the divider character.

        """
        # initialize output
        out = ''
        prepend = ''
        # loop all args like from print
        for arg in args:
            out += f'{prepend}{arg}'
            # set prepend for next value
            prepend = sep

        return (divider * 40 + end if divider else '') + out + end

    def render(self, data: Dict[str, Any], *args: Any, **kw: Any) -> Union[str, None]:
        """
        Render data as text output.

        Takes a data dictionary and renders it as text output. The data dictionary
        should contain an 'args' key with the values to print.

        ### Args:

        - **data** (dict): The data dictionary to render, containing:
            - args: Tuple of arguments to print
            - sep: Separator between items (default: space)
            - end: String to append at the end (default: newline)
            - name: Optional name for logging
            - divider: Optional character for divider line

        - ***args**: Variable length argument list
        - ****kw**: Arbitrary keyword arguments

        ### Returns:

        - **str|None**: Formatted text string, or None if no 'args' key is found

        ### Raises:

        - **KeyError**: If required keys are missing from the data dictionary

        ### Notes:

        : This method checks for the presence of the 'args' key in the data
          dictionary and logs a debug message if it's missing. When rendering,
          it passes the arguments and formatting options to the _print method.

        """
        if 'args' in data.keys():
            name = f" named {data['name']}" if 'name' in data.keys() and data['name'] is not None and data['name'] != '' else ''
            self.app.log.debug(f'rendering content via {self.__module__}{name}')
            return self._print(data['args'], sep=data['sep'], end=data['end'], divider=data['divider'])  # type: ignore
        else:
            self.app.log.debug(f'No "args" key found in data to render. Not rendering content via "{self.__module__}"')
            return None


class TokeoPrintDictOutputHandler(output.OutputHandler):
    """
    Output handler for dictionary printing.

    This class implements the Output Handler interface for dictionary printing.
    It renders dictionaries as key-value pairs, one per line, which is useful
    for development and debugging.

    ### Methods:

    - **render**: Formats and returns dictionary content as text

    ### Notes:

    : This handler is registered with the label 'print_dict' and provides
      specialized rendering for dictionary objects. Unlike the standard print
      handler, it formats each key-value pair on its own line, making it easier
      to read complex dictionary structures.

    : This handler is particularly useful for debugging configuration data,
      API responses, and other structured data formats.

    ### Example:

    ```python
    # Sample dictionary
    config = {
        'debug': True,
        'log_level': 'INFO',
        'max_retries': 3,
        'timeout': 30
    }

    # Render with print_dict handler
    app.render(config, handler='print_dict')
    ```

    """

    class Meta(output.OutputHandler.Meta):
        """
        Handler meta-data configuration.

        ### Notes:

        : This class defines the metadata required by the Cement framework for
          proper handler registration and operation. It specifies how the
          output handler is identified and whether it can be overridden via
          command line options.

        """

        label = 'print_dict'
        """The string identifier of this handler."""

        #: Whether or not to include ``json`` as an available choice
        #: to override the ``output_handler`` via command line options.
        overridable = False

    _meta: Meta  # type: ignore

    def render(self, data: Dict[str, Any], *args: Any, **kw: Any) -> str:
        """
        Render a dictionary as text output.

        Takes a data dictionary and renders it as key-value pairs, one per line,
        creating a more readable format for dictionaries than standard print output.

        ### Args:

        - **data** (dict): The data dictionary to render
        - ***args**: Variable length argument list
        - ****kw**: Arbitrary keyword arguments

        ### Returns:

        - **str**: A text string with one key-value pair per line

        ### Notes:

        : This method logs a debug message when rendering begins, then iterates
          through all keys and values in the dictionary, formatting each as a
          key-value pair on its own line.

        """
        self.app.log.debug(f'rendering content as text via {self.__module__}')
        out = ''
        for key, val in data.items():
            out = out + f'{key}: {val}\n'

        return out


class TokeoInspectOutputHandler(output.OutputHandler):
    """
    Output handler for object inspection.

    This class implements the Output Handler interface for detailed object
    inspection. It can display object values, types, methods, and attributes
    in a structured format, providing a comprehensive view of objects for
    debugging and exploration.

    ### Methods:

    - **render**: Process inspection data and format output
    - **_inspect**: Internal method for detailed object introspection

    ### Notes:

    : This handler is registered with the label 'inspect' and is used by the
      app.inspect() method. It provides detailed introspection of objects,
      showing their values, types, methods, and attributes in a structured format.

    : The handler can inspect multiple objects at once and provides options
      for controlling the level of detail in the output, from basic value
      inspection to comprehensive method and attribute listings.

    """

    class Meta(output.OutputHandler.Meta):
        """
        Handler meta-data configuration.

        ### Notes:

        : This class defines the metadata required by the Cement framework for
          proper handler registration and operation. It specifies how the
          output handler is identified and whether it can be overridden via
          command line options.

        """

        label = 'inspect'
        """The string identifier of this handler."""

        #: Whether or not to include ``json`` as an available choice
        #: to override the ``output_handler`` via command line options.
        overridable = False

    _meta: Meta  # type: ignore

    def _inspect(
        self,
        args,
        name=None,
        system=False,
        methods=False,
        attributes=False,
        values=True,
        types=True,
        divider=None,
    ):
        """
        Format detailed inspection of objects.

        Internal method that performs introspection on objects and formats
        the results as a string, including values, types, methods, and attributes
        based on the provided options.

        ### Args:

        - **args** (tuple): Objects to inspect
        - **name** (str, optional): Optional name for the inspection
        - **system** (bool): Whether to include system methods/attributes (__name__)
        - **methods** (bool): Whether to display object methods
        - **attributes** (bool): Whether to display object attributes
        - **values** (bool): Whether to display object values
        - **types** (bool): Whether to display object types
        - **divider** (str, optional): Character to use for divider line

        ### Returns:

        - **str**: The formatted inspection string

        ### Notes:

        : This method performs a comprehensive inspection of the provided objects.
          For each object, it can show:

            - The object's value (string representation)
            - The object's type
            - A list of the object's methods (excluding system methods by default)
            - A list of the object's attributes (excluding system attributes
              by default)
            - System methods and attributes (those with __name__) when requested

        : The output format varies based on which options are enabled, with
          appropriate spacing and formatting for readability.

        """
        # prepare output
        out = ''
        prepend = ''
        # loop given args
        for arg in args:
            # setup line output
            line = prepend
            prepend = ''
            # add value content
            if name or values:
                if name:
                    line += f'Inspect: {name} = '
                line += f'{arg}'
                if types:
                    t_arg = type(arg).__name__
                    line += f' |:{t_arg}|'
                if name or methods or attributes:
                    line += '\n'
                elif types:
                    prepend = ', '
                else:
                    prepend = ' | '
            # inspect methods
            if methods:
                m_inspect = inspect.getmembers(arg, lambda attr: not (inspect.ismethod(attr)))
                if system:
                    m_system = [m[0] for m in m_inspect if (m[0].startswith('__') and m[0].endswith('__'))]
                    line += f'System methods: {m_system}\n'
                m_filtered = [m[0] for m in m_inspect if not (m[0].startswith('__') and m[0].endswith('__'))]
                line += f'Methods: {m_filtered}'
                prepend = '\n'
            # inspect attributes
            if attributes:
                a_inspect = inspect.getmembers(arg, lambda attr: not (inspect.isroutine(attr)))
                if system:
                    a_system = [a[0] for a in a_inspect if (a[0].startswith('__') and a[0].endswith('__'))]
                    line += f'System attributes: {a_system}\n'
                a_filtered = [a[0] for a in a_inspect if not (a[0].startswith('__') and a[0].endswith('__'))]
                line += f'Attributes: {a_filtered}'
                prepend = '\n'
            # append o
            out += line

        return (divider * 40 + '\n' if divider else '') + out

    def render(self, data: Dict[str, Any], *args: Any, **kw: Any) -> Union[str, None]:
        """
        Render object inspection as text output.

        Takes a data dictionary with inspection parameters and renders the
        inspection results as text output. Supports debug mode for logging
        inspection results instead of returning them.

        ### Args:

        - **data** (dict): The data dictionary with inspection parameters,
          containing:

            - args: Objects to inspect
            - name: Optional name for the inspection
            - system: Whether to include system methods/attributes
            - methods: Whether to display object methods
            - attributes: Whether to display object attributes
            - values: Whether to display object values
            - types: Whether to display object types
            - debug: Whether to output to debug log instead of stdout
            - divider: Character to use for divider line

        - ***args**: Variable length argument list
        - ****kw**: Arbitrary keyword arguments

        ### Returns:

        - **str|None**: A string with inspection results, empty string if
          in debug mode, or None if no 'args' key is found

        ### Notes:

        : This method checks for the presence of the 'args' key in the data
          dictionary and logs a debug message if it's missing. When rendering,
          it passes the arguments and inspection options to the _inspect method.

        :When the 'debug' flag is set, the inspection results are logged to
          the debug log instead of being returned for display.

        """
        if 'args' in data.keys():
            name = f" named {data['name']}" if 'name' in data.keys() and data['name'] is not None and data['name'] != '' else ''
            self.app.log.debug(f'rendering inspect via {self.__module__}{name}')
            out = self._inspect(
                data['args'],
                name=data['name'],
                system=data['system'],
                methods=data['methods'],
                attributes=data['attributes'],
                values=data['values'],
                types=data['types'],
                divider=data['divider'],
            )
            if 'debug' in data.keys() and data['debug'] is not None and data['debug']:
                self.app.log.debug(f'>>>\n{out}')
                return ''
            else:
                return out + '\n'
        else:
            self.app.log.debug(f'No "args" key found in data to render. Not rendering content via "{self.__module__}"')
            return None


def load(app: App) -> None:
    """
    Load the print extension into a Tokeo application.

    Registers the necessary output handlers and extends the application
    with print and inspect functionality.

    ### Args:

    - **app**: The application object to extend

    ### Notes:

    : This function is called automatically by the Cement framework when the
      extension is loaded. It registers three output handlers:

      1. TokeoInspectOutputHandler - For detailed object inspection
      2. TokeoPrintDictOutputHandler - For dictionary formatting
      3. TokeoPrintOutputHandler - For standard printing

    : It also extends the application with app.print() and app.inspect() methods
      via the register_tokeo_print function.

    ### Example:

    ```python
    # In your application configuration:
    class MyApp(App):
        class Meta:
            extensions = [
                'tokeo.ext.print',
                # other extensions...
            ]
    ```
    """
    app.handler.register(TokeoInspectOutputHandler)
    app.handler.register(TokeoPrintDictOutputHandler)
    app.handler.register(TokeoPrintOutputHandler)
    register_tokeo_print(app)
