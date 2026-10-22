from tokeo.core.exc import TokeoError


def required(app, extension, *needed):
    """
    Check that the extensions an extension builds on are enabled.

    ### Args

    - **app** (Application): The Cement application instance
    - **extension** (str): The extension that needs them
    - **needed** (str): The extensions it needs, e.g. ```tokeo.ext.tui.output```

    ### Raises

    - **TokeoError**: If one of them is not enabled

    """
    missing = [name for name in needed if name not in app.ext.get_loaded_extensions()]
    if missing:
        names, them = ' and '.join(missing), 'it' if len(missing) == 1 else 'them'
        raise TokeoError(f'The extension {extension} needs {names}, add {them} to the extensions of the app!')
