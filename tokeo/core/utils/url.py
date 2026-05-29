"""
URL helpers for Tokeo applications.

Small utilities for manipulating url strings, such as merging
authentication credentials into a url's userinfo.

"""

from urllib.parse import urlsplit, urlunsplit, quote, unquote


def overload_with_auth(url=None, auth_identity=None, auth_password=None):
    """
    Merge credentials into the userinfo part of a url.

    Each credential, when set, overrides the matching part the url already
    carried, while an unset value keeps the url's own. Values are
    percent-encoded so special characters survive later url parsing (for
    example by pika's URLParameters).

    ### Args

    - **url** (str, optional): The base url (may already carry userinfo);
        an empty or missing url yields None
    - **auth_identity** (str, optional): Username/identity to force into the
        url userinfo
    - **auth_password** (str, optional): Password to force into the url
        userinfo

    ### Returns

    - **str|None**: The url with the effective userinfo applied, or None when
        no url was given

    """
    # nothing to work on: hand back None so callers can pass it straight on
    if not url:
        return None
    parts = urlsplit(url)
    # decode the userinfo the url already carries (it is stored encoded)
    url_user = unquote(parts.username) if parts.username is not None else None
    url_pass = unquote(parts.password) if parts.password is not None else None
    # a configured value wins over the url, otherwise keep the url's value
    user = auth_identity if auth_identity is not None else url_user
    pw = auth_password if auth_password is not None else url_pass
    # rebuild host[:port]
    host = parts.hostname or ''
    if parts.port is not None:
        host = f'{host}:{parts.port}'
    # rebuild netloc with re-encoded userinfo when any credential is present
    if user is not None or pw is not None:
        userinfo = quote(user or '', safe='')
        if pw is not None:
            userinfo += ':' + quote(pw, safe='')
        netloc = f'{userinfo}@{host}'
    else:
        netloc = host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
