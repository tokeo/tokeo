"""
Web page and UI definition for the application's website.

This module provides a page handler in Tokeo applications using NiceGUI.

"""

from tokeo.ext.appshare import app
from ..components import blocks


ui = app.nicegui.ui
ux = app.nicegui.ux


def page_hello_world():
    """

    This function demonstrates how to create additional pages.

    ### Notes:

    - Uses the standard page layout for consistency
    - Demonstrates adding custom styled content to a page

    """
    with blocks.page(title='Customers administration'):
        ui.label('Hello world!').classes('text-2xl text-orange-500')
