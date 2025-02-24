def isbasetype(obj):
    basetypes = (int, str, float, bool, bytes, list, dict, set, tuple)
    return isinstance(obj, basetypes)


def hasprop(obj, name):
    # Check attribute first
    if hasattr(obj, name):
        return True

    # Handle mappings directly
    if isinstance(obj, dict):
        return name in obj

    # Check containment support and try 'in'
    if hasattr(obj, '__contains__') or hasattr(obj, '__iter__') or hasattr(obj, '__getitem__'):
        try:
            return name in obj
        except (TypeError, KeyError, IndexError):
            return False

    # Default to False for non-containers
    return False


def hasprops(obj, names):
    # repeat for speed to not test obj too often
    if isinstance(obj, dict) or hasattr(obj, '__contains__') or hasattr(obj, '__iter__') or hasattr(obj, '__getitem__'):
        # when iterateable object
        try:
            for name in names:
                if name not in obj:
                    return False
            # all props exist
            return True
        except (TypeError, KeyError, IndexError):
            return False

    # if class
    for name in names:
        if not hasattr(obj, name):
            return False
        # all props exist
        return True

    # default miss
    return False


def anyprop(obj, names):
    # repeat for speed to not test obj too often
    if isinstance(obj, dict) or hasattr(obj, '__contains__') or hasattr(obj, '__iter__') or hasattr(obj, '__getitem__'):
        # when iterateable object
        try:
            for name in names:
                if name in obj:
                    return True
            # all props exist
            return False
        except (TypeError, KeyError, IndexError):
            return False

    # if class
    for name in names:
        if hasattr(obj, name):
            return True

    # default miss
    return False


def getprop(obj, name, **kwargs):
    # if not find the attribute, raise an error
    def _raise_attribute_error():
        raise AttributeError(f"'{type(obj).__name__}' object has no attribute or key '{name}'")

    # Check attribute first
    if hasattr(obj, name) and not isinstance(obj, dict) and not hasattr(obj, '__getitem__'):
        return getattr(obj, name)

    # if no attribute exist, then setup
    # a default if was given
    if 'default' in kwargs or 'fallback' in kwargs:
        has_default = True
        default = kwargs['default'] if 'default' in kwargs else kwargs['fallback']
    else:
        has_default = False
        default = None

    # Handle mappings directly
    if isinstance(obj, dict):
        if name in obj:
            return obj[name]
        return default if has_default else _raise_attribute_error()

    # Check containment support and try 'in'
    if hasattr(obj, '__contains__') or hasattr(obj, '__iter__') or hasattr(obj, '__getitem__'):
        try:
            if name in obj:
                return obj[name]
            return default if has_default else _raise_attribute_error()
        except (TypeError, KeyError, IndexError):
            return default if has_default else _raise_attribute_error()

    # Property not found
    return default if has_default else _raise_attribute_error()


def getitem_first_not_blank(values, default=None):
    if not isinstance(values, (list, tuple)):
        raise TypeError('getitem_first_not_blank expects a list or tuple as input arg')

    for value in values:
        if value is not None:
            # test correct instance type
            if not isinstance(value, str):
                raise TypeError('getitem_first_not_blank expects strings as values')

            if str.strip(value) != '':
                return value

    return default


def getitem_first_not_empty(values, default=None):
    if not isinstance(values, (list, tuple)):
        raise TypeError('getitem_first_not_empty expects a list or tuple as input arg')

    for value in values:
        if value is not None:
            return value

    return default


def default_when_blank(value, default=None):
    if value is None:
        return default

    # test correct instance type
    if not isinstance(value, str):
        raise TypeError('default_when_blank expects a string as input arg')

    if str.strip(value) == '':
        return default

    # seems to be filled
    return value


def default_when_empty(value, default=None):
    if value is None:
        return default

    # fast path returns on instance types
    if isinstance(value, str):
        if str.strip(value) == '':
            return default
        else:
            return value

    # fast path returns on instance types
    if isinstance(value, (list, dict, tuple, set)):
        if len(value) == 0:
            return default
        else:
            return value

    # seems to be filled
    return value
