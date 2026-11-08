"""
Checks that the extensions an extension builds on are enabled.
"""

from tokeo.core.exc import TokeoError


def check_required_extensions(app, extension, *needed):
    """
    Check that the extensions an extension builds on are enabled.

    ### Args

    - **app** (Application): The Cement application instance
    - **extension** (str): The extension that needs them
    - **needed** (str): The extensions it needs, e.g. ```tokeo.ext.tui.output```

    ### Raises

    - **TokeoError**: If one of them is not enabled

    """
    missing_names = [name for name in needed if name not in app.ext.get_loaded_extensions()]
    if missing_names:
        names, them = ' and '.join(missing_names), 'it' if len(missing_names) == 1 else 'them'
        raise TokeoError(f'The extension {extension} needs {names}, add {them} to the extensions of the app!')
