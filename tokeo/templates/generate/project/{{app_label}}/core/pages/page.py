from contextlib import contextmanager
from tokeo.ext.appshare import app
from . import layout


ui = app.nicegui.ui
ux = app.nicegui.ux


# change the default colors
layout.COLORS['bg'] = 'bg-neutral-50'


@contextmanager
def page(title=None):
    """
    container for all app pages
    """
    with layout.page(app_info='Made with tokeo!', nav=nav, footer=footer):
        if title:
            ux.h1(title).classes('text-2xl text-bold my-4')
        yield


def nav():
    """
    define the app navigation
    """
    with layout.nav():
        layout.nav_item(label='Dashboard', href='/', icon='home', icon_classes='text-2xl')
        layout.nav_item(label='Customers', href='/hello-world', icon='supervisor_account')
        layout.nav_item(label='Invoices', on_click=lambda: ui.notify('Notify Message', type='positive'), icon='receipt_long')
        layout.nav_item(label='Assets', icon='assessment')


def footer():
    """
    all elements for app footer
    """
    with layout.footer(
        footer_info='{{ app_name }}',
        footer_copyright='{{ app_copyright }}',
    ):
        with ux.div().classes('flex-grow flex flex-col'):
            ux.a('Products').classes('py-1 text-xs tracking-wider').props('href="#"')
            ux.a('Documentation').classes('py-1 text-xs tracking-wider').props('href="#"')
            ux.a('Updates').classes('py-1 text-xs tracking-wider').props('href="#"')
            ux.a('Wiki').classes('py-1 text-xs tracking-wider').props('href="#"')
        with ux.div().classes('flex-grow flex flex-col'):
            ux.a('Pricing').classes('py-1 text-xs tracking-wider').props('href="#"')
            ux.a('Sales').classes('py-1 text-xs tracking-wider').props('href="#"')
            ux.a('Engineering').classes('py-1 text-xs tracking-wider').props('href="#"')
            ux.a('Showcases').classes('py-1 text-xs tracking-wider').props('href="#"')
        with ux.div().classes('flex-grow flex flex-col'):
            ux.a('About').classes('py-1 text-xs tracking-wider').props('href="#"')
            ux.a('Support').classes('py-1 text-xs tracking-wider').props('href="#"')
            ux.a('Help').classes('py-1 text-xs tracking-wider').props('href="#"')
            ux.a('Disclaimer').classes('py-1 text-xs tracking-wider').props('href="#"')
