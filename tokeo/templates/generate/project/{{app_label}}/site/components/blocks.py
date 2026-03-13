"""
High-level structural components and page wrappers.

This module provides the primary building blocks for composing UI pages in the
Tokeo application. It contains pre-assembled, reusable components such as the
main `page` container, standard `nav` (navigation bars), and `footer` elements.

Developers should import this module into their isolated page functions (inside
`site/pages/`) to wrap their unique content in a consistent, branded layout.

### ⚠️ CRITICAL: Multi-User Safety Architecture

This framework adheres to a strict stateless, multi-user architecture.
Instantiating NiceGUI elements (`ui.button`, `ui.label`, etc.) in the
global scope of any module will cause severe memory leaks and cross-user
state contamination (attaching elements to NiceGUI's background shared client).

### 🛡️ The `guard_user_context()` Tripwire

To protect the framework, **ALL** newly created layout functions, root-level
context managers, or wrappers that execute native NiceGUI `ui.*` commands must
trigger the security guard before rendering.

The guard verifies that the function is being called from safely inside an
active user's page route.

**How to implement:**

```python
from tokeo.ext.nicegui import guard_user_context

def my_new_layout_block():
    # ensure multi-user safety
    guard_user_context()

    # add additional elements
    ui.label('This element is now safe from global leaks')
```

If a guarded function is accidentally called at the global/import level,
the guard will instantly raise a `TokeoNiceguiError` and crash the app on
startup, intentionally preventing the memory leak from ever reaching production.

"""

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
