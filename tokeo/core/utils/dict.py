def deep_merge(a, b):
    """
    Recursively merge two data structures with special handling for different types.

    This function handles merging of dictionaries, lists, and primitive types:

    - Primitives: b replaces a
    - Lists: elements from b are appended to a
    - Dicts: recursively merge keys from b into a

    ### Args:

    - **a** (any): The target data structure that will be modified
    - **b** (any): The source data structure to merge into a

    ### Returns:

    - **any**: The merged data structure (same object as a, modified in-place)

    ### Raises:

    - **ValueError**: If trying to merge incompatible types or unsupported types
    - **ValueError**: If a TypeError occurs during merging (with context details)

    ### Notes:

    : **IMPORTANT**: Tuples and arbitrary objects are not handled as
        their merge behavior would be ambiguous.

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


def redact_data(data, replace_value='***'):
    """
    Recursively redact all values in a data structure by replacing them with
    a placeholder.

    Traverses through dictionaries and lists to replace all scalar values with
    the specified replacement value, preserving the original structure. This is
    useful for logging or displaying sensitive data without exposing
    the actual values.

    ### Args:

    - **data** (dict|list|any): The data structure containing values to be redacted
    - **replace_value** (str, optional): The placeholder to use for redacted values.
        Defaults to '***'

    ### Returns:

    - **dict|list|str**: A new data structure with the same shape as the input,
        but with all scalar values replaced by the redaction placeholder

    ### Notes:

    - Dictionary keys are preserved as-is, only values are redacted
    - The function creates a new data structure and does not modify the original

    """
    if isinstance(data, dict):
        return {k: redact_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [redact_data(item) for item in data]
    else:
        # Replace any scalar value with replace_value
        return replace_value
