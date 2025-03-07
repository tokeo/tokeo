"""
Tokeo Print Extension Module.

This extension provides enhanced printing capabilities for Tokeo applications.
It integrates with the Cement framework to provide structured output handling
for print operations, ensuring that pre_render and post_render hooks are honored.

The extension provides three main pieces of functionality:
1. app.print() - A replacement for Python's built-in print() function
2. app.inspect() - A detailed inspection utility for objects
3. Output handlers for different rendering styles

Example:
    To use this extension in your application:

    .. code-block:: python

        from tokeo.app import TokeoApp

        with TokeoApp('myapp', extensions=['tokeo.ext.print']) as app:
            # Use the print extension
            app.print("Hello, world!")

            # Use the inspect extension
            user = {'name': 'John', 'age': 30}
            app.inspect(user, methods=True, attributes=True)
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
    that use the appropriate output handlers.

    Args:
        app: The application object to extend.
    """

    def _print(*args: any, name=None, sep=' ', end='\n', divider=None) -> None:
        """
        Print output using the application's print handler.

        Args:
            *args: Variable arguments to print.
            name: Optional name to identify this print operation in logs.
            sep: Separator between printed items (default: space).
            end: String to append at the end (default: newline).
            divider: Character to use for divider line (prints 40 of these).
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

        Args:
            *args: Objects to inspect.
            name: Optional name to identify this inspect operation in logs.
            system: Whether to include system methods/attributes.
            methods: Whether to display object methods.
            attributes: Whether to display object attributes.
            values: Whether to display object values.
            types: Whether to display object types.
            debug: Whether to output to debug log instead of stdout.
            divider: Character to use for divider line (prints 40 of these).
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
    """

    class Meta(output.OutputHandler.Meta):
        """
        Handler meta-data configuration.

        Attributes:
            label (str): The string identifier of this handler.
            overridable (bool): Whether this handler can be overridden via CLI.
        """

        label = 'print'
        """The string identifier of this handler."""

        #: Whether or not to include ``json`` as an available choice
        #: to override the ``output_handler`` via command line options.
        overridable = False

    _meta: Meta  # type: ignore

    def _print(self, args, sep=' ', end='\n', divider=None):
        """
        Format arguments for printing.

        Args:
            args: The arguments to print.
            sep: Separator between printed items.
            end: String to append at the end.
            divider: Character to use for divider line.

        Returns:
            str: The formatted string ready for output.
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

        Args:
            data (dict): The data dictionary to render.
            *args: Variable length argument list.
            **kw: Arbitrary keyword arguments.

        Returns:
            str: A text string, or None if no 'args' key is found.
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
    """

    class Meta(output.OutputHandler.Meta):
        """
        Handler meta-data configuration.

        Attributes:
            label (str): The string identifier of this handler.
            overridable (bool): Whether this handler can be overridden via CLI.
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

        Takes a data dictionary and renders it as key-value pairs, one per line.

        Args:
            data (dict): The data dictionary to render.
            *args: Variable length argument list.
            **kw: Arbitrary keyword arguments.

        Returns:
            str: A text string with one key-value pair per line.
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
    in a structured format.
    """

    class Meta(output.OutputHandler.Meta):
        """
        Handler meta-data configuration.

        Attributes:
            label (str): The string identifier of this handler.
            overridable (bool): Whether this handler can be overridden via CLI.
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

        Args:
            args: Objects to inspect.
            name: Optional name for the inspection.
            system: Whether to include system methods/attributes.
            methods: Whether to display object methods.
            attributes: Whether to display object attributes.
            values: Whether to display object values.
            types: Whether to display object types.
            divider: Character to use for divider line.

        Returns:
            str: The formatted inspection string.
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
        inspection results as text output.

        Args:
            data (dict): The data dictionary with inspection parameters.
            *args: Variable length argument list.
            **kw: Arbitrary keyword arguments.

        Returns:
            str: A string with inspection results, None if no 'args' key is found.
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

    Args:
        app: The application object to extend.
    """
    app.handler.register(TokeoInspectOutputHandler)
    app.handler.register(TokeoPrintDictOutputHandler)
    app.handler.register(TokeoPrintOutputHandler)
    register_tokeo_print(app)
