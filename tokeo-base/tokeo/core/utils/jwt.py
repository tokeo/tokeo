"""
Small JWT helpers for tokeo.

Decoding helpers for JSON Web Tokens that do not depend on any auth provider.
Used wherever tokeo needs to read a token's claims (e.g. its expiry) without
pulling in a full JWT library.
"""

import base64
import json
import time


def is_token_valid(token, min_valid_seconds):
    """
    Return whether a JWT stays valid for at least a safety margin.

    Decodes the token's payload and compares its ```exp``` claim against the
    current time plus a margin. A token is considered valid (returns True) only
    when it is well formed, carries an ```exp``` and expires later than
    ```min_valid_seconds``` from now.

    ### Args

    - **token** (str): The JWT to inspect (header.payload.signature)
    - **min_valid_seconds** (int): The minimum remaining lifetime required

    ### Returns

    - **bool**: True if the token stays valid longer than the margin; False if
        it is malformed, carries no ```exp``` or expires within the margin

    """
    parts = token.split('.')
    if len(parts) != 3:
        return False
    try:
        pad = parts[1] + '=' * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad).decode('utf-8'))
        exp = int(payload['exp'])
    except (ValueError, KeyError, TypeError):
        return False
    return exp > time.time() + min_valid_seconds
