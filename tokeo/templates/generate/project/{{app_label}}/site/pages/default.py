"""
Web page and UI definition for the application's website.

This module provides a page handler in Tokeo applications using NiceGUI.

"""

from tokeo.ext.appshare import app
from ..components import blocks


ui = app.nicegui.ui
ux = app.nicegui.ux


def page_default():
    """

    This function defines the content of the website's landing page (index route).
    It uses the standard page layout from the blocks module and adds
    homepage-specific content.

    ### Notes:

    - Creates a dashboard view with introductory content

    """
    with blocks.page(title='This is the {{ app_name }} dashboard'):
        ux.p(
            """
            This single-page "app" style layout features a sidebar, main content area, and footer.
            This full-height layout is never more than viewport height. The content area scrolls
            independently as needed. For this example, we're using the Tailwind CSS utility framework.
            As part of it's default classes, Tailwind includes Flexbox classes which make this layout
            implementation simple!
            """
        ).classes('text-lg')
