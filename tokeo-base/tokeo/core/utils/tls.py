"""
TLS helpers for Tokeo applications.

Small utilities for building ssl contexts for client connections from
simple configuration values and for creating certificates in memory, so
connection handlers and services share one well-tested implementation.

"""

import ssl
import ipaddress
from datetime import datetime, timedelta, timezone

# the curves of an ec key by its size
_EC_CURVE_NAMES = {256: 'SECP256R1', 384: 'SECP384R1', 521: 'SECP521R1'}


def create_ssl_context(verify_hostname=True, verify_cert=True, ca_path=None):
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
    - **ca_path** (str, optional): Path to a CA bundle to trust instead of
        the system store (load_verify_locations)

    ### Returns

    - **tuple**: (context, server_hostname); both None when defaults apply

    ### Reminders

    .. warning::

        verify_cert=False accepts any certificate and exposes the connection
        to man-in-the-middle attacks; use it only for local development.

    """
    server_hostname = verify_hostname if isinstance(verify_hostname, str) else None
    # all secure defaults: let the caller's own library build the context
    if verify_hostname is True and verify_cert is True and ca_path is None:
        return None, None
    context = ssl.create_default_context(cafile=ca_path)
    if not verify_cert:
        # order matters: check_hostname must be off before relaxing verify_mode
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif verify_hostname is False:
        context.check_hostname = False
    # a string verify_hostname keeps check_hostname on and pins the name via
    # the returned server_hostname
    return context, server_hostname


def create_certificate(common_name, sans=(), key_type='ec', key_size=384, valid_days=90, is_ca=False, issuer=None):
    """
    Create a certificate and its key in memory, self-signed or signed by a CA.

    ### Args

    - **common_name** (str): The subject common name
    - **sans** (iterable, optional): Subject alt names; an IP address becomes
        an IP entry, any other value a DNS name; none without
    - **key_type** (str, optional): ```ec``` or ```rsa```. Defaults to ec.
    - **key_size** (int, optional): 256, 384 or 521 for ec, 2048 or more for
        rsa. Defaults to 384.
    - **valid_days** (int, optional): Days the certificate is valid from now.
        Defaults to 90.
    - **is_ca** (bool, optional): Create a CA that signs other certificates.
        Defaults to False.
    - **issuer** (tuple, optional): ```(certificate, key)``` of the CA that
        signs; self-signed without

    ### Returns

    - **tuple**: ```(certificate, key, cert_pem, key_pem)```, the pem as bytes

    ### Raises

    - **ValueError**: On a key_type other than ec or rsa, a key_size not valid
        for it or valid_days that is no integer greater than 0

    ### Notes

    : Every certificate has a random serial number, a subject key identifier
        and an authority key identifier, as strict chain checks require.

    : A CA may sign certificates and still serve as a server certificate; a
        leaf certificate serves a server and a client.

    : The cryptography package is imported here, only creating a
        certificate needs it.

    """
    # lazy: only creating a certificate needs cryptography
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    if key_type == 'ec':
        if key_size not in _EC_CURVE_NAMES:
            raise ValueError(f'key_size {key_size!r} is not valid for ec, use 256, 384 or 521')
        key = ec.generate_private_key(getattr(ec, _EC_CURVE_NAMES[key_size])())
    elif key_type == 'rsa':
        if type(key_size) is not int or key_size < 2048:
            raise ValueError(f'key_size {key_size!r} is not valid for rsa, use 2048 or greater')
        key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    else:
        raise ValueError(f"key_type must be 'ec' or 'rsa', not {key_type!r}")
    if type(valid_days) is not int or valid_days < 1:
        raise ValueError(f'valid_days must be an integer greater than 0, not {valid_days!r}')

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    issuer_cert, issuer_key = issuer if issuer else (None, key)
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_cert.subject if issuer_cert else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=valid_days))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                # an rsa key also encrypts the key exchange, an ec key only signs
                key_encipherment=key_type == 'rsa',
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=is_ca,
                crl_sign=is_ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
    )
    if not is_ca:
        extended_usages = [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
        builder = builder.add_extension(x509.ExtendedKeyUsage(extended_usages), critical=False)
    alt_names = []
    for san in dict.fromkeys(sans):
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(san)))
        except ValueError:
            alt_names.append(x509.DNSName(san))
    if alt_names:
        builder = builder.add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
    certificate = builder.sign(issuer_key, hashes.SHA256())
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return certificate, key, cert_pem, key_pem
