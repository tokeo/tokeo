"""
Tokeo PocketBase Module.

A driver for the REST API of PocketBase 0.40 and later, built on httpx. It
reads and writes records, builds filters with safely quoted values, logs in
with a password, refreshes and impersonates, sends batches and checks the
health of the server. ```tokeo.core.pocketbase.realtime``` follows the
changes of records.

### Example

```python
from tokeo.core.pocketbase import PocketBase

with PocketBase('http://127.0.0.1:8090') as pb:
    pb.auth_with_password('users', 'service@example.com', 'secret')
    posts = pb.collection('posts')
    post = posts.create({'title': 'Hello', 'views': 0})
    found = posts.get_list(filter=pb.filter('title = {:title}', {'title': "it's"}))
    print(post.id, found.total_items)
```

### Notes

: Each PocketBase instance holds its own http client and its own token, an
    instance logged in as one identity never acts for another one

: Every error of the server or of the connection raises
    ```TokeoPocketBaseError``` with the status (0 without an answer), the
    url and the data of the answer

: Build a filter from outside values only by ```filter()``` with
    placeholders; it quotes and escapes the values for the filter syntax

: PocketBase takes a refused or expired token without an error, as a
    guest: lists come back empty, realtime events stop. Keep a login by an
    ```AuthKeeper``` as ```before_request```, it confirms the login on the
    server and logs in anew

.. include:: ./POCKETBASE.md
"""

from .exc import TokeoPocketBaseError, TokeoPocketBaseAuthError
from .record import Record, ListResult
from .filter import build_filter, path_segment
from .client import PocketBase, AuthKeeper
from .collection import Collection
from .batch import Batch

__all__ = [
    'TokeoPocketBaseError',
    'TokeoPocketBaseAuthError',
    'Record',
    'ListResult',
    'build_filter',
    'path_segment',
    'PocketBase',
    'AuthKeeper',
    'Collection',
    'Batch',
]
