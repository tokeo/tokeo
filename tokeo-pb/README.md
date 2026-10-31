# Tokeo PB

The [PocketBase](https://pocketbase.io) driver of [Tokeo](https://github.com/tokeo/tokeo) for the REST API of PocketBase 0.40 and later, built on [httpx](https://www.python-httpx.org), like reading and writing records, filters with safely quoted values, login, refresh and impersonation, batches in one transaction and the health check.

<br/>

## Features

- Records to list, page through, get, create, update and delete, with ```expand```, ```fields``` and ```sort```
- Files uploaded along with the fields of a record
- Filters built with placeholders, each value quoted and escaped for the filter syntax of PocketBase
- Login with password, token refresh and impersonation of a record by a superuser
- Batches of record changes in one transaction, all or nothing
- Each client with its own connections and its own login
- Records as dicts that read their fields also as attributes

<br/>

#### Why not the PocketBase SDK for Python

PocketBase itself offers SDKs for JavaScript and Dart only. The SDK for Python is a translation of the one for JavaScript by the community. Its filter helper escapes the quote but not the backslash, so a value ending in a backslash breaks out of its string, and it fails on a token without expiry. Tokeo PB covers what Tokeo needs directly on the REST API and quotes filter values as the filter parser of PocketBase reads them.

<br/>

## Getting Started

### Installing Tokeo PB

```bash
# Install directly from Pypi.org
pip install tokeo-pb
```

### Usage

```python
from tokeo.core.pocketbase import PocketBase

with PocketBase('http://127.0.0.1:8090') as pb:
    pb.auth_with_password('users', 'service@example.com', 'secret')
    posts = pb.collection('posts')
    post = posts.create({'title': "it's new", 'views': 0})
    found = posts.get_list(filter=pb.filter('title = {:title}', {'title': "it's new"}))
    print(post.id, found.total_items)

    # one transaction, all or nothing
    batch = pb.batch()
    batch.update('posts', post.id, {'views': 1})
    batch.create('posts', {'title': 'second'})
    batch.send()
```

<br/>
<br/>

It depends on tokeo-base and httpx. Within a Tokeo app use the pocketbase extension ```tokeo.ext.pocketbase```.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
