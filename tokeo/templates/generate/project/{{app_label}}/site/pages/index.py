"""
Homepage UI component for the application's website.

Provides the UI implementation for the root dashboard view. Constructed as an
isolated, stateless module to comply with NiceGUI 3.x multi-user routing
requirements.

"""

from tokeo.ext.appshare import app
from ..components import blocks


ui = app.nicegui.ui
ux = app.nicegui.ux


def page_root():
    """
    Construct and render the website's landing page (index route).

    This function is executed dynamically by the Tokeo router whenever a user
    navigates to the root path. It initializes local UI elements specific to
    the active user session. In addition add hompage-specific

    ### Notes:

    - Creates a dashboard view with introductory content
    - Uses the standard page layout from the `blocks` module
    - Must not be called directly; it is orchestrated by `site/routes.py`

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
