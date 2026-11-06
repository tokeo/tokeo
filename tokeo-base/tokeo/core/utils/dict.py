"""
Merges nested data deeply and redacts its values before they are logged.
"""

import logging

#: The logger of this module
LOG = logging.getLogger(__name__)


def deep_merge(a, b):
    """
    Recursively merge two data structures with special handling for different types.

    This function handles merging of dictionaries, lists, and primitive types:

    - Single values (str, number, date, VaultRef, ...): b replaces a
    - Lists: elements from b are appended to a
    - Dicts: recursively merge keys from b into a

    ### Args

    - **a** (any): The target data structure that will be modified
    - **b** (any): The source data structure to merge into a

    ### Returns

    - **any**: The merged data structure (same object as a, modified in-place)

    ### Raises

    - **ValueError**: If a dict gets a value that is no dict
    - **ValueError**: If a TypeError occurs during merging (with context details)

    ### Notes

    : A value that is no list and no dict is replaced by b as a whole,
        e.g. a date or an encrypted VaultRef of another config file

    : Lists are always appended, never replaced or de-duplicated. Merging
        the same b into a more than once (or re-merging an already merged
        result) therefore grows the list with duplicate entries each time.
        Callers needing replace-or-dedupe semantics should clear or
        post-process the list themselves, or a future variant could take an
        explicit list strategy (append vs. replace).

    : ```a``` is merged in place and is also returned, so the original ```a``` is
        modified by the call and should not be assumed to stay untouched.

    : For new keys, appended list items and primitive replacements the
        result keeps references to b's objects instead of copying them, so
        the merged result and b can share the same nested mutable objects;
        a later merge into the result, or a change to b, may then overwrite
        values on the other side unexpectedly.

    : If a caller needs its inputs encapsulated, it should pass copies,
        e.g. deep_merge(a, copy.deepcopy(b)); copying b keeps the result
        free of references into the caller's b.

    """
    # track the current dict key so the TypeError handler can name it;
    # stays None when the failure is not inside the dict-merge loop
    key = None
    try:
        if a is None or not isinstance(a, (list, dict)):
            # border case for first run or a single value: b replaces it
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
                # the values only for debug, they may hold secrets
                LOG.debug(f'deep_merge: cannot merge {b!r} into {a!r}')
                raise ValueError(f'Cannot merge a {type(b).__name__} into a dict')
    except TypeError as e:
        # only name a key when the failure actually happened in the loop
        in_key = f' in key "{key}"' if key is not None else ''
        LOG.debug(f'deep_merge: TypeError {e}{in_key} when merging {b!r} into {a!r}')
        raise ValueError(f'TypeError "{e}"{in_key} when merging a {type(b).__name__} into a {type(a).__name__}')
    return a


def redact_data(data, replace_value='***'):
    """
    Recursively redact all values in a data structure by replacing them with
    a placeholder.

    Traverses through dictionaries and lists to replace all scalar values with
    the specified replacement value, preserving the original structure. This is
    useful for logging or displaying sensitive data without exposing
    the actual values.

    ### Args

    - **data** (dict|list|any): The data structure containing values to be redacted
    - **replace_value** (str, optional): The placeholder to use for redacted values.
        Defaults to '***'

    ### Returns

    - **dict|list|str**: A new data structure with the same shape as the input,
        but with all scalar values replaced by the redaction placeholder

    ### Notes

    - Dictionary keys are preserved as-is, only values are redacted
    - The function creates a new data structure and does not modify the original

    """
    if isinstance(data, dict):
        return {k: redact_data(v, replace_value) for k, v in data.items()}
    elif isinstance(data, list):
        return [redact_data(item, replace_value) for item in data]
    else:
        # Replace any scalar value with replace_value
        return replace_value
