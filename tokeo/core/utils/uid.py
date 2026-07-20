"""
Small helpers that hand out a random id, with an optional prefix.

Kept dependency-free so any consumer -- a tool, a governor, a project's own
code -- can mint an id for a value that needs one (e.g. a code-originated tool
call, where no provider supplies the id). The prefix lets the caller mark the
id's origin (```'inj_'``` for an injected call); with no prefix the bare token
is returned.
"""

import secrets
import uuid


def get_token_hex(length=8, prefix=None):
    """
    Hand out a random hex token of ```length``` bytes, optionally prefixed.

    The byte count sets the collision margin: 4 bytes (32 bit) is plenty for a
    handful of ids that only need to be unique within one turn; 8 bytes
    (64 bit) leaves a huge margin; 16 bytes (128 bit) matches a uuid's entropy.
    A larger token does not cost meaningfully more to make.

    ### Args

    - **length** (int): The number of random bytes; use 4, 8 or 16. The hex
        string is twice that many characters
    - **prefix** (str, optional): Prepended to the token when it is a string;
        with ```None``` or ```''``` the bare token is returned

    ### Returns

    - **str**: ```prefix``` + the hex token, or the bare token

    ### Raises

    - **TypeError**: If prefix is neither a string nor None

    """
    token = secrets.token_hex(length)
    if prefix is None:
        return token
    if isinstance(prefix, str):
        return f'{prefix}{token}'
    raise TypeError(f'prefix must be a str or None, got {type(prefix).__name__}')


def get_uuid4(prefix=None):
    """
    Hand out a random uuid4 (hex, no dashes), optionally prefixed.

    A uuid4 carries 122 random bits -- the conservative choice when an id must
    be unique well beyond a single turn. For a turn-local id ```get_token_hex```
    is shorter and enough.

    ### Args

    - **prefix** (str, optional): Prepended to the uuid when it is a string;
        with ```None``` or ```''``` the bare uuid hex is returned

    ### Returns

    - **str**: ```prefix``` + the uuid hex, or the bare uuid hex

    ### Raises

    - **TypeError**: If prefix is neither a string nor None

    """
    token = uuid.uuid4().hex
    if prefix is None:
        return token
    if isinstance(prefix, str):
        return f'{prefix}{token}'
    raise TypeError(f'prefix must be a str or None, got {type(prefix).__name__}')
