"""
Cement print extension module.
"""

from __future__ import annotations
from typing import Any, Dict, Union, TYPE_CHECKING
from cement.core import output
from cement.utils.misc import minimal_logger

if TYPE_CHECKING:
    from cement.core.foundation import App  # pragma: nocover

import inspect


def tokeo_print(app: App) -> None:

    def _print(*args: any, name=None, sep=' ', end='\n') -> None:
        app.render(dict(args=args, name=name, sep=sep, end=end), handler='print')

    app.extend('print', _print)

    def _inspect(*args: any, name=None, system=False, methods=False, attributes=False, values=True, types=True, debug=False) -> None:
        app.render(
            dict(args=args, name=name, system=system, methods=methods, attributes=attributes, values=values, types=types, debug=debug),
            handler='inspect',
        )

    app.extend('inspect', _inspect)


class TokeoPrintOutputHandler(output.OutputHandler):
    """
    This class implements the :ref:`Output <cement.core.output>` Handler
    interface.  It takes a dict and only prints out the ``out`` key. It is
    primarily used by the ``app.print()`` extended function in order to replace
    ``print()`` so that framework features like ``pre_render`` and
    ``post_render`` hooks are honored. Please see the developer documentation
    on :cement:`Output Handling <dev/output>`.

    """

    class Meta(output.OutputHandler.Meta):
        """Handler meta-data"""

        label = 'print'
        """The string identifier of this handler."""

        #: Whether or not to include ``json`` as an available choice
        #: to override the ``output_handler`` via command line options.
        overridable = False

    _meta: Meta  # type: ignore

    def _print(self, args, sep=' ', end='\n'):
        # initialize output
        out = ''
        prepend = ''
        # loop all args like from print
        for arg in args:
            out += f'{prepend}{arg}'
            # set prepend for next value
            prepend = sep

        return out + end

    def render(self, data: Dict[str, Any], *args: Any, **kw: Any) -> Union[str, None]:
        """
        Take a data dictionary and render only the ``out`` key as text output.
        Note that the template option is received here per the interface,
        however this handler just ignores it.

        Args:
            data (dict): The data dictionary to render.

        Returns:
            str: A text string.

        """
        if 'args' in data.keys():
            name = f" named {data['name']}" if 'name' in data.keys() and data['name'] is not None and data['name'] != '' else ''
            self.app.log.debug(f'rendering content via {self.__module__}{name}')
            return self._print(data['args'], sep=data['sep'], end=data['end'])  # type: ignore
        else:
            self.app.log.debug("no 'args' key found in data to render. " 'not rendering content via %s' % self.__module__)
            return None


class TokeoPrintDictOutputHandler(output.OutputHandler):
    """
    This class implements the :ref:`Output <cement.core.output>` Handler
    interface.  It is intended primarily for development where printing out a
    string reprisentation of the data dictionary would be useful.  Please see
    the developer documentation on :cement:`Output Handling <dev/output>`.

    """

    class Meta(output.OutputHandler.Meta):
        """Handler meta-data"""

        label = 'print_dict'
        """The string identifier of this handler."""

        #: Whether or not to include ``json`` as an available choice
        #: to override the ``output_handler`` via command line options.
        overridable = False

    _meta: Meta  # type: ignore

    def render(self, data: Dict[str, Any], *args: Any, **kw: Any) -> str:
        """
        Take a data dictionary and render it as text output.  Note that the
        template option is received here per the interface, however this
        handler just ignores it.

        Args:
            data (dict): The data dictionary to render.

        Returns:
            str: A text string.

        """
        self.app.log.debug(f'rendering content as text via {self.__module__}')
        out = ''
        for key, val in data.items():
            out = out + f'{key}: {val}\n'

        return out


class TokeoInspectOutputHandler(output.OutputHandler):
    """
    This class implements the :ref:`Output <cement.core.output>` Handler
    interface.  It takes a dict and only prints out the ``out`` key. It is
    primarily used by the ``app.inspect()`` extended function in order to replace
    ``inspect()`` so that framework features like ``pre_render`` and
    ``post_render`` hooks are honored. Please see the developer documentation
    on :cement:`Output Handling <dev/output>`.

    """

    class Meta(output.OutputHandler.Meta):
        """Handler meta-data"""

        label = 'inspect'
        """The string identifier of this handler."""

        #: Whether or not to include ``json`` as an available choice
        #: to override the ``output_handler`` via command line options.
        overridable = False

    _meta: Meta  # type: ignore

    # build inspect output
    def _inspect(self, args, name=None, system=False, methods=False, attributes=False, values=True, types=True):
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

        return out

    def render(self, data: Dict[str, Any], *args: Any, **kw: Any) -> Union[str, None]:
        """
        Take a data dictionary and render only the ``out`` key as text output.
        Note that the template option is received here per the interface,
        however this handler just ignores it.

        Args:
            data (dict): The data dictionary to render.

        Returns:
            str: A text string.

        """

        if 'args' in data.keys():
            name = f" named {data['name']}" if 'name' in data.keys() and data['name'] is not None and data['name'] != '' else ''
            self.app.log.debug(f'rendering inspect via {self.__module__}{name}')
            out = self._inspect(data['args'], name=data['name'], system=data['system'], methods=data['methods'], attributes=data['attributes'], values=data['values'], types=data['types'])  # type: ignore
            if 'debug' in data.keys() and data['debug'] is not None and data['debug']:
                self.app.log.debug(f'>>>\n{out}')
                return ''
            else:
                return out + '\n'
        else:
            self.app.log.debug("no 'args' key found in data to inspect. " 'not rendering inspect via %s' % self.__module__)
            return None


def load(app: App) -> None:
    app.handler.register(TokeoInspectOutputHandler)
    app.handler.register(TokeoPrintDictOutputHandler)
    app.handler.register(TokeoPrintOutputHandler)
    app.hook.register('pre_argument_parsing', tokeo_print)
