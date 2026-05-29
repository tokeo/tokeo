"""
TLS helpers for Tokeo applications.

Small utilities for building ssl contexts for client connections from
simple configuration values, so connection handlers can share one
well-tested implementation.

"""

import ssl


def create_ssl_context(verify_hostname=True, verify_cert=True, ca=None):
    """
    Build a client ssl context from simple verification options.

    Returns a (context, server_hostname) pair. The context is None when all
    options are at their secure defaults, so the caller can let its own
    library set up TLS (for example pika's default amqps handling). A
    returned context is built from ssl.create_default_context, which verifies
    the certificate chain and the hostname unless relaxed here.

    ### Args

    - **verify_hostname** (bool|str): True verifies the certificate hostname;
        False skips that check while still verifying the chain; a string
        keeps the check but pins the expected name, returned as
        server_hostname for the caller to apply at connect time
    - **verify_cert** (bool): True requires a valid certificate chain; False
        disables verification entirely (CERT_NONE)
    - **ca** (str, optional): Path to a CA bundle to trust instead of the
        system store (load_verify_locations)

    ### Returns

    - **tuple**: (context, server_hostname); both None when defaults apply

    ### Reminders

    .. warning::
        verify_cert=False accepts any certificate and exposes the connection
        to man-in-the-middle attacks; use it only for local development.

    """
    server_hostname = verify_hostname if isinstance(verify_hostname, str) else None
    # all secure defaults: let the caller's own library build the context
    if verify_hostname is True and verify_cert is True and ca is None:
        return None, None
    context = ssl.create_default_context(cafile=ca)
    if not verify_cert:
        # order matters: check_hostname must be off before relaxing verify_mode
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif verify_hostname is False:
        context.check_hostname = False
    # a string verify_hostname keeps check_hostname on and pins the name via
    # the returned server_hostname
    return context, server_hostname
