"""
Low-level layout primitives, styling configurations, and the theme engine.

This module serves as the foundational design system for the application.
It defines core responsive breakpoints (aligned with Tailwind CSS), color
palettes (`COLORS` dictionary), base CSS injections, and the lowest-level
HTML wrappers.

While `blocks.py` provides high-level composition, this module dictates
*how* those blocks look and behave. To re-theme the application or adjust
global padding, routing behavior, and typography, modify the configurations
in this file.

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
from tokeo.ext.nicegui import guard_user_context


ui = app.nicegui.ui
ux = app.nicegui.ux


# defalut coloring, can be overwritten
COLORS = dict(
    bg='bg-neutral-50',
    sidebar_bg='bg-neutral-50',
    app_info='text-emerald-600',
    app_info_bg='bg-slate-200',
    nav='text-slate-800',
    nav_bg='bg-slate-200',
    nav_hover='text-slate-50',
    nav_hover_bg='bg-emerald-600',
    main='text-slate-800',
    footer='text-slate-50',
    footer_bg='bg-slate-600',
)


# align screen sizes to tailwind sizes
app.nicegui.fastapi_app.config.quasar_config['screen'] = {
    'xs': 378,
    'sm': 640,
    'md': 768,
    'lg': 1024,
    'xl': 1280,
    '2xl': 1536,
}


def css_inject():
    ui.add_head_html(
        """
        <style>
            .nicegui-content {
                display: block;
                --nicegui-default-padding: 0;
                --nicegui-default-gap: 0;
            }

            .colum, .flex, .row {
                flex-wrap: unset;
            }

            @media (min-width: 640px) {
                .sidebar-max-width {
                    max-width: 24rem;
                }
            }

        </style>
        """
    )


@contextmanager
def page(
    app_info=None,
    nav=None,
    footer=None,
):
    # ensure multi-user safety
    guard_user_context()

    # update some nicegui / quasar basics
    css_inject()

    # app container
    with ux.div().classes(f'flex flex-col h-screen overflow-hidden {COLORS["bg"]}'):

        # inner container
        with ux.div().classes('w-full flex flex-col sm:flex-row flex-grow overflow-hidden'):

            # left side bar
            with ux.div().classes(f'sidebar-max-width sm:w-1/3 md:1/4 w-full flex-shrink flex-grow-0 p-4 {COLORS["sidebar_bg"]}'):

                # upper app info
                if app_info:
                    with ux.div().classes(f'{COLORS["app_info_bg"]} rounded-xl border mb-3 w-full'):
                        with ux.div().classes(
                            'max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:py-12 lg:px-8 lg:flex lg:items-center lg:justify-between'
                        ):
                            with ux.h2().classes('text-3xl font-extrabold tracking-tight sm:text-4xl'):
                                ux.span(app_info).classes(f'block {COLORS["app_info"]} overflow-ellipsis')

                # navigation
                if nav:
                    nav()

            # main content (scrolled)
            with ux.div().classes(f'w-full h-full flex-grow p-3 {COLORS["main"]} overflow-auto'):
                yield

        # check for footer
        if footer:
            footer()


def nav_item(
    label='Label',
    height='h-auto',
    classes='',
    href=None,
    new_tab=False,
    on_click=None,
    icon='link',
    icon_classes='text-2xl',
):
    # ensure multi-user safety
    guard_user_context()

    with ux.li().classes(f'hover:!{COLORS["nav_hover"]} hover:{COLORS["nav_hover_bg"]} rounded flex items-center {height}').style():
        if href:
            action = (
                ux.div().classes(f'py-2 truncate w-full {classes}').on('click', lambda href=href: ui.navigate.to(href, new_tab=new_tab))
            )
        elif on_click:
            action = ux.div().classes(f'py-2 truncate w-full {classes}').on('click', on_click)
        else:
            action = ux.div().classes(f'py-2 truncate w-full {classes}')
        with action:
            # check for icon in nav element
            if icon and icon != '':
                # add the icon by material icons
                ui.icon(icon).classes(f'w-7 sm:mx-2 mx-4 text-2xl {icon_classes}')
            # define the nav label
            ux.span(label).classes('inline max-sm:hidden')


@contextmanager
def nav():
    # ensure multi-user safety
    guard_user_context()

    with ux.div().classes(f'p-4 {COLORS["nav"]} {COLORS["nav_bg"]} rounded-xl w-full'):
        with ux.ul().classes('flex sm:flex-col overflow-hidden content-center justify-between'):
            yield


@contextmanager
def footer(footer_info=None, footer_copyright='{{ app_copyright }}'):
    # ensure multi-user safety
    guard_user_context()

    # sticky footer
    with ux.footer().classes(f'{COLORS["footer_bg"]} mt-auto'):
        with ux.div().classes(f'px-4 py-3 {COLORS["footer"]} mx-auto'):
            # title when not mobile
            if footer_info:
                ux.h2(footer_info).classes('text-2xl block max-sm:hidden mb-6')

            # footer content
            with ux.div().classes('flex mb-4'):
                yield

            # bottom copyright
            with ux.div().classes('text-center text-xs py-2'):
                ux.a(footer_copyright).classes('').props('href="#')
