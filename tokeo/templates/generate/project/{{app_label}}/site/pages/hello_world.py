"""
Demonstration UI component for the application's website.

Constructed as an isolated, stateless module to comply with NiceGUI 3.x
multi-user routing requirements.

"""

from tokeo.ext.appshare import app
from ..components import blocks


ui = app.nicegui.ui
ux = app.nicegui.ux


def page_hello_world():
    """
    Construct and render the hello world demonstration page.

    This function is executed dynamically by the Tokeo router. It demonstrates
    adding custom styled content within the shared layout block.

    ### Notes:

    - Uses the standard page layout for UI consistency
    - Demonstrates adding custom styled content to a page

    """
    with blocks.page(title='Customers administration'):
        ui.label('Hello world!').classes('text-2xl text-orange-500')
