import re
import shlex
from .base import hasprop


def keyword(s):
    if isinstance(s, str):
        return re.sub(r'[^a-zA-Z0-9_]', '', s)
    else:
        raise ValueError('No string given to sanitize keyword.')


def is_keyword(s):
    return s == keyword(s)


def quoted(s):
    if s is None:
        return s

    try:
        return shlex.quote(str(s))
    except Exception as e:
        raise ValueError(f'Error to sanitize by quote. {e}')


def keyword_dict(d):
    if d is None:
        return d

    if not hasprop(d, 'items'):
        raise ValueError('No iterable dict given to sanitize dict.')

    _sanitize_d = dict()
    for k, v in d.items():
        if is_keyword(k):
            _sanitize_d[k] = quoted(v)

    return _sanitize_d
