"""
Load a local file in place of an installed module, e.g. a fixed copy of a
library module until its next release. Kept apart from
tokeo.core.utils.modules, so only an import from here loads it.

### Example

```python
from pathlib import Path
from tokeo.core.utils.modules.override import override_module

# a fixed copy of pocketbase/utils.py, its validate_token raises KeyError
# on a token without exp
override_module('pocketbase.utils', Path(__file__).with_name('pocketbase_utils.py'))
```

### Notes

: Call it before anything imports the library, also its parent package,
    e.g. in the ```__init__.py``` of the app package; an imported module is
    refused, its users would keep the old one

: Every import of the name gets the local file, also the one of its own
    parent package, so a library module loaded by its package works too

: The file runs as the module, with its relative imports resolved against
    the parent package; only override with a file of your own

: Pin the version of the library while an override is active, the copy is
    taken from that version

"""

import sys
import importlib
import importlib.abc
import importlib.util
from pathlib import Path
from tokeo.core.utils.modules import TokeoModulesLoadError


class TokeoModulesOverrideFinder(importlib.abc.MetaPathFinder):
    """
    Serve a local file whenever the overridden module is imported.

    ### Args

    - **name** (str): The dotted name of the overridden module
    - **file_path** (Path): The python file to load as that module

    """

    def __init__(self, name, file_path):
        #: The dotted name of the overridden module
        self.name = name
        #: The python file to load as that module
        self.file_path = file_path

    def find_spec(self, fullname, path=None, target=None):
        """
        Hand out the spec of the local file for the overridden name.

        ### Args

        - **fullname** (str): The dotted name of the module to import
        - **path** (list|None): The search path of the parent package
        - **target** (module|None): The module to reload, if any

        ### Returns

        - **ModuleSpec|None**: The spec of the local file, None for any
            other name

        """
        # only the overridden name, every other import goes its usual way
        if fullname != self.name:
            return None
        return importlib.util.spec_from_file_location(fullname, self.file_path)


def override_module(name, file_path):
    """
    Load a local file as the module name, in place of the installed one.

    ### Args

    - **name** (str): The dotted name of the module, e.g. ```pocketbase.utils```
    - **file_path** (str|Path): The python file to load as that module

    ### Returns

    - **module**: The loaded module

    ### Raises

    - **TokeoModulesLoadError**: If the file is missing or the module is
        imported already

    """
    file_path = Path(file_path)
    if not file_path.is_file():
        raise TokeoModulesLoadError(f'file {str(file_path)!r} to override {name!r} does not exist')
    if name in sys.modules:
        raise TokeoModulesLoadError(f'module {name!r} is imported already, override it before its first import')
    finder = TokeoModulesOverrideFinder(name, file_path)
    sys.meta_path.insert(0, finder)
    try:
        # load it now, so an error shows here and not at a later import
        return importlib.import_module(name)
    except BaseException:
        # a failed load leaves neither the finder nor a half module behind
        sys.meta_path.remove(finder)
        sys.modules.pop(name, None)
        raise
