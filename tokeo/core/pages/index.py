from tokeo.ext.appshare import app

ui = app.nicegui.ui
ux = app.nicegui.ux


@app.nicegui.fastapi_app.get('/api')
async def get_api():
    return {'msg': 'json api result'}


@ui.page('/hello-world')
def hello_function():
    ui.label('Hello world!').classes('text-2xl', 'm-2')


def default():
    ux.h1('This is the homepage!').classes('text-2xl m-2')
