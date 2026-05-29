import re
import shlex
from .base import hasprop


def keyword(s):
    """
    Reduce a string to a safe keyword by stripping every character that
    is not a letter, digit or underscore.

    ### Args

    - **s** (str): The string to sanitize

    ### Returns

    - **str**: The input with all non-keyword characters removed

    ### Raises

    - **ValueError**: If s is not a string

    """
    if isinstance(s, str):
        return re.sub(r'[^a-zA-Z0-9_]', '', s)
    else:
        raise ValueError('No string given to sanitize keyword.')


def is_keyword(s):
    """
    Check whether a string already is a safe keyword.

    ### Args

    - **s** (str): The string to test

    ### Returns

    - **bool**: True if s is left unchanged by keyword(), False otherwise

    ### Raises

    - **ValueError**: If s is not a string (raised by keyword())

    """
    return s == keyword(s)


def quoted(s):
    """
    Shell-quote a value so it can be embedded safely in a command line.

    ### Args

    - **s** (any): The value to quote; non-strings are converted via str()

    ### Returns

    - **str|None**: The shell-quoted string, or None when s is None

    ### Raises

    - **ValueError**: If the value cannot be quoted

    """
    if s is None:
        return s

    try:
        return shlex.quote(str(s))
    except Exception as e:
        raise ValueError(f'Error to sanitize by quote. {e}')


def keyword_dict(d):
    """
    Build a sanitized copy of a mapping with keyword keys and shell-quoted
    values.

    ### Args

    - **d** (dict|None): The mapping to sanitize; None is passed through

    ### Returns

    - **dict|None**: A new dict whose values are shell-quoted, or None when
        d was None

    ### Raises

    - **ValueError**: If d is not None and exposes no items() interface

    ### Notes

    : Keys that are not already safe keywords are dropped on purpose, so the
        result only ever contains keyword-safe keys; callers that need every
        key preserved must sanitize the keys themselves before calling

    """
    if d is None:
        return d

    if not hasprop(d, 'items'):
        raise ValueError('No iterable dict given to sanitize dict.')

    _sanitize_d = dict()
    for k, v in d.items():
        if is_keyword(k):
            _sanitize_d[k] = quoted(v)

    return _sanitize_d
