from tokeo.ext.appshare import app
from .page import page


ui = app.nicegui.ui
ux = app.nicegui.ux


def default():
    with page(title='This is the {{ app_name }} dashboard'):
        ux.p(
            """
            This single-page "app" style layout features a sidebar, main content area, and footer.
            This full-height layout is never more than viewport height. The content area scrolls
            independently as needed. For this example, we're using the Tailwind CSS utility framework.
            As part of it's default classes, Tailwind includes Flexbox classes which make this layout
            implementation simple!
            """
        ).classes('text-lg')


@ui.page('/hello-world')
def hello_function():
    with page(title='Customers administration'):
        ui.label('Hello world!').classes('text-2xl text-orange-500')
