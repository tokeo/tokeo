from functools import wraps
import re
import ast


def pdoc_replace_decorator(*args, **kwargs):
    def decorator(func):
        @wraps(func)
        def _decorator():
            pass

        return _decorator

    return decorator


class DecoratedFunction:

    def __init__(self, app, func, update_func_docstring=True, prepend_docstrings=None, append_docstrings=None):
        self.app = app
        self.func = func
        self.decorators = []
        self._setup_decorators()
        if update_func_docstring:
            self.update_func_docstring(prepend_docstrings=prepend_docstrings, append_docstrings=append_docstrings)

    def __getattr__(self, attr):
        if attr == 'decorators':
            return self.decorators
        else:
            return getattr(self.func, attr)  # Delegate all calls to original function

    @property
    def has_decorators(self):
        return len(self.decorators) > 0

    def _setup_decorators(self):
        block_start = re.search(rf'\s*{self.func.funcdef()}', self.func.source, re.MULTILINE)
        if block_start is None:
            return None

        # get the source block from zero to function definition and
        # replace by simplest method just for ast parsing
        block = f'{self.func.source[:block_start.end()]} decorator(): pass'
        try:
            # safe parse the block
            tree = ast.parse(block)
            # test tree for decorators
            if getattr(tree.body[0], 'decorator_list', None) is None:
                return False
        except Exception:
            return False

        # fill decorators
        for decorator in tree.body[0].decorator_list:
            _err = None
            _decorator = None
            _args = None
            _kwargs = None
            try:
                if isinstance(decorator, ast.Call):
                    # collect for a call
                    _m = []
                    _f = decorator.func
                    # get method full name
                    while isinstance(_f, ast.Attribute):
                        _m.insert(0, _f.attr)
                        _f = _f.value
                    if isinstance(_f, ast.Name):
                        _decorator = '.'.join([f'@{_f.id}'] + _m)
                    # get args for call
                    _args = decorator.args
                    # get kwargs for call
                    _kwargs = {}
                    for kw in decorator.keywords:
                        _kwargs[kw.arg] = kw.value

                elif isinstance(decorator, ast.Name):
                    # collect name only
                    _decorator = f'@{decorator.id}'

            except Exception as err:
                # save error
                _err = err

            # test if decorator was identified
            if _decorator is None:
                if _err is not None:
                    self.app.log.error(_err)
                self.app.log.debug(block)
                continue

            # clear value for additonal docstring
            _params = None
            _docstring = None

            # send out hook to process other modules for decorators docstring
            for res in self.app.hook.run('tokeo_pdoc_render_decorator', self.app, _decorator, _args, _kwargs):
                if res is not None:
                    if isinstance(res, dict):
                        _decorator = res['decorator'] if 'decorator' in res else None
                        _params = res['params'] if 'params' in res else None
                        _docstring = res['docstring'] if 'docstring' in res else None
                    elif isinstance(res, str):
                        _decorator = res
                    else:
                        self.app.log.error('Hook function for tokeo_pdoc_render_decorator returns invalid result')
                        _decorator = None
                    break

            # append to stack
            if _decorator is not None:
                self.decorators.append(
                    dict(
                        decorator=_decorator.strip(),
                        params=None if _params is None else _params.strip(),
                        docstring=None if _docstring is None else _docstring.strip(),
                    )
                )

        # signal success
        return True

    def update_func_docstring(self, prepend_docstrings=None, append_docstrings=None):
        # do insert same decorator's docstring twice
        _unique = {}
        # initialize docstring placeholder
        _docstring = ''
        # loop and get additional docstrings
        for decorator in self.decorators:
            if decorator['docstring'] is not None and decorator['docstring'] != '' and decorator['decorator'] not in _unique:
                # set marker for unique
                _unique[decorator['decorator']] = True
                # update _docstring
                _docstring += (
                    f'{"" if prepend_docstrings is None else prepend_docstrings}'
                    f'{decorator["docstring"]}'
                    f'{"" if append_docstrings is None else append_docstrings}'
                )

        # check for content
        if _docstring != '':
            self.func.docstring = f'{self.func.docstring}{_docstring}'
