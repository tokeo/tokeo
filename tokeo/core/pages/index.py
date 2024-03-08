from tokeo.ext.appshare import app
from tokeo.core.pages import layout
from .components import page


ui = app.nicegui.ui
ux = app.nicegui.ux


@app.nicegui.fastapi_app.get('/api')
async def get_api():
    return {'msg': 'json api result'}


@ui.page('/hello-world')
def hello_function():
    with page():
        ui.label('Hello world!').classes('text-2xl text-orange-500')


def default():
    with page():
        ux.p(
        """
            This single-page "app" style layout features a sidebar, main content area, and footer.
            This full-height layout is never more than viewport height. The content area scrolls
            independently as needed. For this example, we're using the Tailwind CSS utility framework.
            As part of it's default classes, Tailwind includes Flexbox classes which make this layout
            implementation simple!
        """
        ).classes('text-lg')
