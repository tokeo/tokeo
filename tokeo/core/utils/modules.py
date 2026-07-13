import os
import types
import importlib
from tokeo.core.exc import TokeoError


def get_module_path(module):
    """
    Retrieves the absolute filesystem path for a given Python module.

    Accepts either a module object or a string module name, imports the module
    if necessary, and returns its absolute path on the filesystem.

    ### Args

    - **module** (types.ModuleType|str): Module object or string module name

    ### Returns

    - **str|None**: Absolute directory path of the module; for a package
      that is its ```__path__``` directory, for a plain module the directory
      that contains its file. None when neither is available (e.g. a
      namespace package or a builtin without a file)

    ### Raises

    - **TokeoError**: If the provided object is neither a module nor a string

    ### Notes

    : For packages with multiple paths, only the first path is returned

    """
    # check input type
    if isinstance(module, types.ModuleType):
        obj = module
    elif isinstance(module, str):
        obj = importlib.import_module(module)
    else:
        raise TokeoError(f"Can't use {module} as module to get the path")
    # packages expose __path__ (a list of directories); use the first entry
    path = getattr(obj, '__path__', None)
    if path:
        return os.path.abspath(path[0]) if (len(path[0]) > 0) else None
    # plain modules have no __path__ but a __file__; fall back to the
    # directory that contains the module file so both kinds resolve alike
    file = getattr(obj, '__file__', None)
    if file:
        return os.path.abspath(os.path.dirname(file))
    # neither path nor file: nothing sensible to return
    return None
