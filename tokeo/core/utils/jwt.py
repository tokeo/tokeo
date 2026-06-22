"""
Small JWT helpers for tokeo.

Decoding helpers for JSON Web Tokens that do not depend on any auth provider.
Used wherever tokeo needs to read a token's claims (e.g. its expiry) without
pulling in a full JWT library.
"""

import base64
import json
import time


def token_expires_within(token, min_valid_seconds):
    """
    Return whether a JWT is unusable within a safety margin.

    Decodes the token's payload and compares its ``exp`` claim against the
    current time plus a margin. A token is considered unusable (returns True)
    when it is malformed, carries no ``exp``, is already expired, or expires
    within ``min_valid_seconds``.

    ### Args

    - **token** (str): The JWT to inspect (header.payload.signature)
    - **min_valid_seconds** (int): The minimum remaining lifetime required

    ### Returns

    - **bool**: True if the token is malformed or expires within the margin

    """
    parts = token.split('.')
    if len(parts) != 3:
        return True
    try:
        pad = parts[1] + '=' * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad).decode('utf-8'))
        exp = int(payload['exp'])
    except (ValueError, KeyError, TypeError):
        return True
    return exp <= time.time() + min_valid_seconds
