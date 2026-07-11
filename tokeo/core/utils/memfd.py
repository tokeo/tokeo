"""
Filesystem paths backed by memory, for APIs that only accept a path.

Some C-backed stdlib APIs -- notably ```ssl.SSLContext.load_cert_chain``` --
take only a filesystem path and offer no way to pass PEM data from memory, a
gap open in CPython since 2012 (issue 16487). This helper bridges it: it writes
bytes to an anonymous, RAM-backed file descriptor and hands back the kernel
path to it, so the data never touches disk.

- Linux: ```os.memfd_create``` + ```/proc/self/fd/N``` (a seekable anon file).
- macOS/BSD: an anonymous pipe fed by a thread + ```/dev/fd/N```.
- Elsewhere (e.g. Windows): ```MemfdUnavailable``` is raised.

If CPython ever gains in-memory certificate loading, callers can drop this
module and pass the PEM strings directly.
"""

import os
import threading
from contextlib import contextmanager


class MemfdUnavailable(RuntimeError):
    """Raised when the platform offers no memory-backed path mechanism."""


def _feed_and_close(fd, data):
    """Write all bytes to a pipe fd then close it (runs in a feeder thread)."""
    try:
        os.write(fd, data)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


@contextmanager
def memory_paths(*blobs):
    """
    Yield one RAM-backed filesystem path per blob; nothing touches disk.

    Each path is valid only inside the ```with``` block. Pass separate blobs
    for values that a consumer opens independently (e.g. a certificate and its
    key): the macOS/BSD backing is a pipe, which is read once and not seekable,
    so a single combined path cannot be reopened.

    ### Args

    - ***blobs** (bytes|str): The payloads; each ```str``` is encoded as UTF-8

    ### Yields

    - **tuple[str]**: One path per blob, in the given order

    ### Raises

    - **MemfdUnavailable**: If the platform has neither ```os.memfd_create```
        nor ```/dev/fd``` (e.g. Windows)

    """
    data = [b if isinstance(b, (bytes, bytearray)) else str(b).encode() for b in blobs]

    if hasattr(os, 'memfd_create'):
        fds = []
        try:
            paths = []
            for blob in data:
                fd = os.memfd_create('tokeo-mem', 0)
                fds.append(fd)
                os.write(fd, blob)
                os.lseek(fd, 0, os.SEEK_SET)
                paths.append(f'/proc/self/fd/{fd}')
            yield tuple(paths)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
        return

    if os.path.isdir('/dev/fd'):
        fds = []
        threads = []
        try:
            paths = []
            for blob in data:
                r, w = os.pipe()
                t = threading.Thread(target=_feed_and_close, args=(w, blob), daemon=True)
                t.start()
                fds.append(r)
                threads.append(t)
                paths.append(f'/dev/fd/{r}')
            yield tuple(paths)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            for t in threads:
                t.join(timeout=1)
        return

    raise MemfdUnavailable('no os.memfd_create and no /dev/fd on this platform')
