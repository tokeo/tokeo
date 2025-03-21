import types
import importlib
from cement.utils.fs import abspath
from tokeo.core.exc import TokeoError


def get_module_path(module):
    """
    Retrieves the absolute filesystem path for a given Python module.

    Accepts either a module object or a string module name, imports the module
    if necessary, and returns its absolute path on the filesystem.

    ### Args:

    - **module** (types.ModuleType|str): Module object or string module name

    ### Returns:

    - **str|None**: Absolute path to the module directory, or None if the module
      doesn't have a path

    ### Raises:

    - **TokeoError**: If the provided object is neither a module nor a string

    ### Notes:

    : For packages with multiple paths, only the first path is returned

    """
    # check input type
    if isinstance(module, types.ModuleType):
        obj = module
    elif isinstance(module, str):
        obj = importlib.import_module(module)
    else:
        raise TokeoError(f'Can\'t use {module} as module to get the path')
    # get the path of the module
    return abspath(obj.__path__[0]) if (len(obj.__path__) > 0) else None
