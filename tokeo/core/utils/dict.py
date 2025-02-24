def deep_merge(a, b):
    """
    IMPORTANT NOTE:

    tuples and arbitrary objects are not handled
    as it is totally ambiguous what should happen
    """
    try:
        if a is None or isinstance(a, (str, float, int)):
            # border case for first run or if a is a primitive
            a = b
        elif isinstance(a, list):
            # lists will be only appended
            if isinstance(b, list):
                a.extend(b)
            else:
                a.append(b)
        elif isinstance(a, dict):
            # dicts will be merged
            if isinstance(b, dict):
                for key in b:
                    if key in a:
                        a[key] = deep_merge(a[key], b[key])
                    else:
                        a[key] = b[key]
            else:
                raise ValueError(f'Cannot merge non-dict "{b}" into dict "{a}"')
        else:
            raise ValueError(f'NOT IMPLEMENTED "{b}" into "{a}"')
    except TypeError as e:
        raise ValueError(f'TypeError "{e}" in key "{key}" when merging "{b}" into "{a}"')
    return a
