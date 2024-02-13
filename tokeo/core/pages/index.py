from nicegui import ui


@ui.page('/hello-world')
def hello_function3():
    ui.label('Hello world!').tailwind('text-2xl', 'm-2')


def default():
    ui.label('This is the homepage!').tailwind('text-2xl', 'm-2')
