"""
Tokeo SMTPD TLS Transport Module.

A translation of MidiSmtpServer ```tls-transport.rb```: it builds the
server ```ssl.SSLContext``` for STARTTLS from a certificate/key (or a generated
self-signed test certificate when none is configured). The default ciphers and
TLS floor follow the current Mozilla "Intermediate" profile (TLS 1.2+, ECDHE
before DHE, AEAD only); the classic method/cipher options can still be supplied.

### Notes

: The default TLS floor is TLS 1.2 (the RFC 8996 minimum); the maximum stays
    at the runtime's highest, so TLS 1.3 is used when available. Use
    ```methods='TLSv1_3'``` for a TLS 1.3-only listener, or the opt-in
    ```TLS_METHODS_LEGACY```/```TLS_CIPHERS_LEGACY``` presets to reach old
    (deprecated TLS 1.0/1.1) clients for opportunistic SMTP TLS.
: TLS 1.3 key-exchange groups are not restricted, so a runtime built on
    OpenSSL 3.5+ negotiates the post-quantum hybrid (X25519MLKEM768) with no
    code change. Do not pin a single curve, or the hybrid group is disabled.
: A configured certificate is loaded with the ```ssl``` stdlib (chain and an
    embedded key are handled by ```load_cert_chain```). A self-signed test
    certificate is generated in-process with ```cryptography``` and loaded from
    memory, so no subprocess and no temp file is involved. A certificate can
    also be supplied as PEM content in memory.

"""

import os
import ssl
import ipaddress
from enum import Enum, auto
from datetime import datetime, timedelta, timezone


from .logger import Severity
from tokeo.core.utils.memfd import memory_paths


# Cipher strings and TLS floor presets, roughly Mozilla Modern / Intermediate /
# Old. The defaults (ADVANCED*) follow the current "Intermediate" profile (ECDHE
# preferred over DHE, AEAD only, ChaCha20 for clients without AES-NI); see
# https://ssl-config.mozilla.org and the OWASP TLS Cipher String Cheat Sheet.
TLS_CIPHERS_ADVANCED_PLUS = (
    'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:'
    'ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:'
    'ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:'
    'DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384:DHE-RSA-CHACHA20-POLY1305'
)
TLS_CIPHERS_ADVANCED = (
    TLS_CIPHERS_ADVANCED_PLUS
    + ':ECDHE-ECDSA-AES128-SHA256:ECDHE-RSA-AES128-SHA256'
    + ':ECDHE-ECDSA-AES256-SHA384:ECDHE-RSA-AES256-SHA384'
    + ':DHE-RSA-AES128-SHA256:DHE-RSA-AES256-SHA256'
)
# LEGACY: the widest compatible set that still keeps forward secrecy -- it adds
# the SHA1 CBC suites needed by TLS 1.0/1.1 clients, but deliberately excludes
# the broken/legacy suites (3DES/Sweet32, RC4, DES, MD5, static-RSA). Opt-in.
TLS_CIPHERS_LEGACY = (
    TLS_CIPHERS_ADVANCED
    + ':ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA'
    + ':ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA'
    + ':DHE-RSA-AES128-SHA:DHE-RSA-AES256-SHA'
)
# TLS floor presets. The default (ADVANCED) is TLS 1.2, the RFC 8996 minimum for
# a secure server. MODERN is TLS 1.3 only. LEGACY drops the floor to TLS 1.0 for
# maximum client/server interoperability -- an explicit opt-in trade-off (TLS
# 1.0/1.1 are deprecated) that is still useful for opportunistic SMTP TLS, where
# an old, weakly-encrypted hop beats a cleartext one.
TLS_METHODS_MODERN = 'TLSv1_3'
TLS_METHODS_ADVANCED = 'TLSv1_2'
TLS_METHODS_LEGACY = 'TLSv1'

# An unknown method falls back to the secure TLS 1.2 default; the maximum is left
# at the runtime's highest (TLS 1.3). TLS 1.3 key-exchange groups are not
# restricted, so a runtime with OpenSSL 3.5+ negotiates the post-quantum hybrid
# (X25519MLKEM768) automatically.
_TLS_VERSIONS = {
    'TLSv1': ssl.TLSVersion.TLSv1,
    'TLSv1_1': ssl.TLSVersion.TLSv1_1,
    'TLSv1_2': ssl.TLSVersion.TLSv1_2,
    'TLSv1_3': ssl.TLSVersion.TLSv1_3,
}


class EncryptMode(Enum):
    """
    The encrypt_mode symbols (member names match the config values).

    ### Notes

    - ```TLS_FORBIDDEN```: no STARTTLS offered at all
    - ```TLS_OPTIONAL```: the client may upgrade via STARTTLS
    - ```TLS_WHEN_AUTH```: like ```TLS_OPTIONAL```, but AUTH is only offered
        and accepted on an encrypted channel. Protects credentials against
        a plaintext downgrade
    - ```TLS_REQUIRED```: every dialog beyond EHLO/STARTTLS demands TLS

    """

    TLS_FORBIDDEN = auto()
    TLS_OPTIONAL = auto()
    TLS_WHEN_AUTH = auto()
    TLS_REQUIRED = auto()


class TlsTransport:
    """
    Builds and holds the server ```SSLContext``` for STARTTLS.

    ### Args

    - **cert_path** (str, optional): PEM certificate (chain) path; when unset a
        self-signed test certificate is generated
    - **key_path** (str, optional): PEM private-key path; when unset the key is
        read from ```cert_path``` (embedded key)
    - **cert** (str|bytes, optional): PEM certificate (chain) content in memory;
        takes precedence over ```cert_path``` (Tokeo-only extension)
    - **key** (str|bytes, optional): PEM private-key content in memory; when unset
        the key is read from the ```cert``` content (embedded key)
    - **ciphers** (str, optional): OpenSSL cipher string
    - **methods** (str, optional): TLS floor; ```'TLSv1_2'``` (default) /
        ```'TLSv1_3'``` (TLS 1.3 only) / ```'TLSv1'``` (opt-in, reaches
        deprecated TLS 1.0/1.1 clients). Unknown values fall back to TLS 1.2
    - **cert_cn** (str, optional): Common name for the self-signed certificate
    - **cert_san** (list|str, optional): Subject alt names for the self-signed
        certificate
    - **logger** (callable, optional): ```log(severity, msg)``` sink

    """

    def __init__(self, cert_path=None, key_path=None, cert=None, key=None, ciphers=None, methods=None,
                 cert_cn='', cert_san=None, logger=None):
        self._logger = logger
        cert_path = (cert_path or '').strip() or None
        key_path = (key_path or '').strip() or None
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # PROTOCOL_TLS_SERVER already sets OP_NO_COMPRESSION (CRIME) and
        # OP_CIPHER_SERVER_PREFERENCE; also refuse client-initiated TLS 1.2
        # renegotiation (a DoS vector) where the runtime supports it
        if hasattr(ssl, 'OP_NO_RENEGOTIATION'):
            self.context.options |= ssl.OP_NO_RENEGOTIATION
        self.context.set_ciphers(ciphers if ciphers else TLS_CIPHERS_ADVANCED_PLUS)
        self.context.minimum_version = _TLS_VERSIONS.get(methods or TLS_METHODS_ADVANCED, ssl.TLSVersion.TLSv1_2)
        if cert is not None:
            # certificate content given in memory (Tokeo-only extension):
            # load it via RAM-backed paths so nothing is written to disk
            self._load_from_memory(cert, key)
        elif cert_path is None:
            # if none cert_path was set, create a self signed test certificate
            self._load_self_signed(str(cert_cn or '').strip(), cert_san)
        else:
            # if any is set, test the paths and load the certificate (+ chain)
            if not os.path.isfile(cert_path):
                raise FileNotFoundError(f'File "{cert_path}" does not exist. Could not load certificate.')
            if key_path is not None and not os.path.isfile(key_path):
                raise FileNotFoundError(f'File "{key_path}" does not exist. Could not load private key.')
            # load_cert_chain handles a chained cert file and an embedded key
            self.context.load_cert_chain(cert_path, key_path)

    def _load_from_memory(self, cert, key):
        """
        Load an in-memory PEM certificate (+ key) via RAM-backed paths.

        ```ssl.SSLContext.load_cert_chain``` only accepts filesystem paths;
        ```memory_paths``` bridges that with an anonymous, RAM-backed fd so the
        certificate never touches disk. When ```key``` is None the key is read
        from the ```cert``` content (embedded key).

        """
        blobs = (cert,) if key is None else (cert, key)
        with memory_paths(*blobs) as paths:
            self.context.load_cert_chain(paths[0], paths[1] if key is not None else None)

    def _log(self, severity, msg):
        """Best-effort log via the injected sink."""
        if self._logger is not None:
            try:
                self._logger(severity, msg)
            except Exception:  # noqa: B902 - logging must never break setup
                pass

    def _load_self_signed(self, cert_cn, cert_san):
        """
        Generate a self-signed test certificate and load it.

        ### Notes

        : RSA 4096, 90-day validity, SHA256, subject CN and subject alt names
            (DNS for every SAN plus IP entries for those that are IP addresses),
            generated in-process with ```cryptography``` and loaded from memory

        """
        # lazy: the cryptography package is only needed to GENERATE the
        # test certificate; loading configured certificates is stdlib ssl
        from cryptography import x509
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        # normalize the subject alt name list
        if cert_san is None:
            cert_san = []
        elif isinstance(cert_san, str):
            cert_san = [s.strip() for s in cert_san.split(',') if s.strip()]
        cn = cert_cn or 'localhost'
        sans = list(dict.fromkeys([cn] + list(cert_san)))
        # DNS for every SAN, plus IP entries for the ones that are IP addresses
        alt_names = [x509.DNSName(san) for san in sans]
        for san in sans:
            try:
                alt_names.append(x509.IPAddress(ipaddress.ip_address(san)))
            except ValueError:
                pass
        self._log(Severity.DEBUG, f'SSL: using self generated test certificate! CN={cn} SAN=[{",".join(sans)}]')
        # generate the key and the self-signed certificate
        key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            # the subject and the issuer are identical only for test certificate
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(now)
            # valid for 90 days
            .not_valid_after(now + timedelta(days=90))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=False)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=True,
                    data_encipherment=False, key_agreement=False, key_cert_sign=False,
                    crl_sign=False, encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False,
            )
            .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
            .sign(key, hashes.SHA256())
        )
        cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        # load the generated cert + key from memory (never touches disk)
        self._load_from_memory(cert_pem, key_pem)
