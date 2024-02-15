from tokeo.ext.appshare import app

ui = app.nicegui.ui
tw = app.nicegui.tw


@app.nicegui.fastapi_app.get('/api')
async def get_api():
    return {'msg': 'json api result'}


@ui.page('/hello-world')
def hello_function():
    ui.label('Hello world!').tailwind('text-2xl', 'm-2')


def default():
    tw.h1('text-2xl m-2', text='This is the homepage!')
