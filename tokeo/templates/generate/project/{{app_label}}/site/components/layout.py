from contextlib import contextmanager
from tokeo.ext.appshare import app


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
    with ux.div().classes(f'p-4 {COLORS["nav"]} {COLORS["nav_bg"]} rounded-xl w-full'):
        with ux.ul().classes('flex sm:flex-col overflow-hidden content-center justify-between'):
            yield


@contextmanager
def footer(footer_info=None, footer_copyright='{{ app_copyright }}'):
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
