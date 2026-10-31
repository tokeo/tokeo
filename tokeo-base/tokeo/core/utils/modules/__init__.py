"""
Tokeo Modules Module.

Loads modules and objects that a config names, with one form and one error
for all extensions. An object is named as ```module:attr```, like an entry
point; the dotted form ```module.attr``` is read as well.

### Example

```python
from tokeo.core.utils.modules import resolve_module, resolve_object

actors = resolve_module('myapp.core.tasks.actors')
servicer = resolve_object('myapp.core.grpc.myapp_servicer:MyappServicer')
```

### Notes

: Every failure raises ```TokeoModulesLoadError```, a TokeoError, so main
    reports it as a clean message; the cause stays attached

: A module that a config names runs as code, so only trusted configs may
    name modules

: The override of an installed module by a local file lives apart in
    ```tokeo.core.utils.modules.override```, it is loaded only from there

"""

import os
import types
import importlib
import importlib.util
from tokeo.core.exc import TokeoError
from tokeo.core.utils.validate import is_filled_str


class TokeoModulesLoadError(TokeoError):
    """
    Raised when a module or an object named in the config cannot be loaded.

    ### Notes

    : Derives from TokeoError, so main reports it as a clean message

    """

    pass


def resolve_module(name):
    """
    Import a module named in the config.

    ### Args

    - **name** (str): The dotted name of the module

    ### Returns

    - **module**: The imported module

    ### Raises

    - **TokeoModulesLoadError**: If name is no filled string or the module
        does not import

    """
    if not is_filled_str(name):
        raise TokeoModulesLoadError(f'{name!r} is no module name')
    try:
        return importlib.import_module(name)
    except ImportError as err:
        raise TokeoModulesLoadError(f'module {name!r} does not import: {err}') from err


def resolve_object(name):
    """
    Import the object a config names as module:attr or module.attr.

    ### Args

    - **name** (str): ```pkg.module:attr``` or ```pkg.module.attr```; after
        the colon the attr may be dotted, e.g. ```pkg.module:Class.method```

    ### Returns

    - **any**: The object, e.g. a function or a class

    ### Raises

    - **TokeoModulesLoadError**: If name has no module or attr part, the
        module does not import or it has no such attr

    ### Notes

    : ```module:attr``` is the form to use; the dotted form is split at its
        last dot and kept for older configs

    : The module part is imported as named, so an import error inside the
        module shows as such and is not hidden

    """
    if not is_filled_str(name):
        raise TokeoModulesLoadError(f'{name!r} is no name of module:attr')
    module, sep, attr = name.partition(':')
    if not sep:
        module, _, attr = name.rpartition('.')
    if not module or not attr:
        raise TokeoModulesLoadError(f'{name!r} must be set as module:attr')
    obj = resolve_module(module)
    try:
        for part in attr.split('.'):
            obj = getattr(obj, part)
    except AttributeError as err:
        raise TokeoModulesLoadError(f'{name!r} could not be found: {err}') from err
    return obj


def find_module_dir(name):
    """
    Find the directory of a module without running it.

    ### Args

    - **name** (str): The dotted name of the module

    ### Returns

    - **str|None**: The directory of the module file, None if not found

    ### Notes

    : Only the parent packages are imported to find it, the module itself
        does not run, so a module with an error is found as well

    """
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin or not os.path.isfile(spec.origin):
        return None
    return os.path.dirname(os.path.abspath(spec.origin))


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
