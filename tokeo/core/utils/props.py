"""
Tokeo Props Module.

Read an attribute of an object or a key of a dict or container by one call,
whatever the kind of the object is.
"""


def hasprop(obj, name):
    """
    Check if an object has a property or key with the given name.

    Works with different object types:

    - For classes: checks if the attribute exists
    - For dicts: checks if the key exists
    - For containers: checks if the item exists

    ### Args

    - **obj** (any): The object to check
    - **name** (str): The property/key name to look for

    ### Returns

    - **bool**: True if the property exists, False otherwise

    """
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
    """
    Check if an object has all the properties or keys in the given list.

    Works with different object types:

    - For classes: checks if all attributes exist
    - For dicts and containers: checks if all keys/items exist

    ### Args

    - **obj** (any): The object to check
    - **names** (list|tuple): List of property/key names to look for

    ### Returns

    - **bool**: True if all properties exist, False otherwise

    """
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
    # all names checked and present; an empty names list is vacuously true,
    # which matches the dict/container branch above
    return True


def anyprop(obj, names):
    """
    Check if an object has any of the properties or keys in the given list.

    Works with different object types:

    - For classes: checks if any attribute exists
    - For dicts and containers: checks if any key/item exists

    ### Args

    - **obj** (any): The object to check
    - **names** (list|tuple): List of property/key names to look for

    ### Returns

    - **bool**: True if any property exists, False otherwise

    """
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
    """
    Get a property value from an object, with flexible object type handling.

    Works with different object types:

    - For classes: gets the attribute value
    - For dicts: gets the key value
    - For containers: gets the item value

    Supports default/fallback values if the property doesn't exist.

    ### Args

    - **obj** (any): The object to get the property from
    - **name** (str): The property/key name to get

    ### Keyword Args

    - **default**: Value to return if property doesn't exist
    - **fallback**: Alternative name for default

    ### Returns

    - **any**: The property value or default if not found

    ### Raises

    - **AttributeError**: If property doesn't exist and no default provided

    """

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
