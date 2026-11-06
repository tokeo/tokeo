# Tokeo PocketBase

The [PocketBase](https://pocketbase.io) driver of [Tokeo](https://github.com/tokeo/tokeo) for the REST API of PocketBase 0.40 and later, built on [httpx](https://www.python-httpx.org), like records to search, read and write, filters with safely quoted values, login, refresh and impersonation, batches in one transaction, the realtime events of changed records and the health check.

<br/>

## Features

- Records to list, page through, find, get, create, update and delete, with ```expand```, ```fields``` and ```sort```
- Files uploaded along with the fields of a record
- Filters with placeholders, each value quoted and escaped for the filter syntax of PocketBase
- Login with password, token refresh and impersonation of a record by a superuser
- A hook before each request, e.g. an ```AuthKeeper``` that logs in on first use, renews the token and confirms the login on the server, since PocketBase takes a refused token as a guest without an error
- Batches of record changes in one transaction, all or nothing, once the batch api is switched on in PocketBase
- Realtime events of created, updated and deleted records, in threads of their own or as tasks of an asyncio loop, connected again after a loss and stopped without a thread or connection left behind
- Realtime that never makes PocketBase hold events for a slow callback, and that shows a lost login as error instead of running on as a silent guest
- Each client with its own connections and its own login
- Records as dicts that read and write their fields also as attributes
- Errors with the status, the url and the invalid fields of the answer

### Why not the PocketBase SDK for Python

PocketBase itself offers SDKs for JavaScript and Dart only. The SDK for Python is a translation of the one for JavaScript by the community. Its filter helper escapes the quote but not the backslash, so a value ending in a backslash breaks out of its string, and it fails on a token without expiry. Tokeo PocketBase covers what Tokeo needs directly on the REST API and quotes filter values as the filter parser of PocketBase reads them.

<br/>

## Getting Started

### Installing Tokeo PocketBase

```bash
# Install directly from Pypi.org
pip install tokeo-pocketbase
```

### Usage

Log in as a user, search the open orders of a customer and mark them as paid.

```python
from tokeo.core.pocketbase import PocketBase, TokeoPocketBaseError

with PocketBase('http://127.0.0.1:8090') as pb:
    # log in as a user of the auth collection users
    pb.auth_with_password('users', 'anna@example.com', 'anna-secret-123')
    orders = pb.collection('orders')

    # search with placeholders, the values are quoted and escaped
    open_orders = orders.get_list(
        filter=pb.filter('customer = {:customer} && status = {:status}', {'customer': "O'Brien", 'status': 'open'}),
        sort='invoice',
    )
    print(f'{open_orders.total_items} open orders')

    # change one field of each record, the others stay
    for order in open_orders.items:
        paid = orders.update(order.id, {'status': 'paid'})
        print(paid.invoice, paid.status)

    # a record that is not there raises with its status
    try:
        orders.get_one('missing00000001')
    except TokeoPocketBaseError as err:
        print(err.status)
```

It prints:

```
2 open orders
RE-1 paid
RE-2 paid
404
```

### Realtime

Follow the changes of the orders while the example above runs in a second shell. The events come by the login of the client, so only the records its rules let it see.

```python
import time
from tokeo.core.pocketbase import PocketBase
from tokeo.core.pocketbase.realtime import Realtime

def on_order(event):
    print(event.action, event.record.invoice, event.record.status)

with PocketBase('http://127.0.0.1:8090') as pb:
    pb.auth_with_password('users', 'anna@example.com', 'anna-secret-123')
    with Realtime(pb) as realtime:
        realtime.subscribe('orders/*', on_order)
        time.sleep(60)
```

It prints:

```
update RE-1 paid
update RE-2 paid
```

A lost connection is opened again and ```on_connect``` is called, the place to reload what was missed meanwhile. Within asyncio, e.g. nicegui, ```AsyncRealtime``` runs as task and also awaits coroutines.

A slow callback never makes PocketBase hold events, and a lost login shows as error instead of a silent guest; the guide explains both.

### Try it

Start PocketBase from its [releases](https://github.com/pocketbase/pocketbase/releases) with a superuser:

```bash
./pocketbase superuser upsert admin@example.com admin-secret-123
./pocketbase serve
```

Then create the collection orders, the user and three orders once as superuser, and run the example above:

```python
from tokeo.core.pocketbase import PocketBase

with PocketBase('http://127.0.0.1:8090') as pb:
    pb.auth_with_password('_superusers', 'admin@example.com', 'admin-secret-123')
    # signed in users may list, view and update the orders
    rule = '@request.auth.id != ""'
    pb.send('POST', '/api/collections', json={
        'name': 'orders',
        'type': 'base',
        'listRule': rule,
        'viewRule': rule,
        'updateRule': rule,
        'fields': [
            {'name': 'invoice', 'type': 'text'},
            {'name': 'customer', 'type': 'text'},
            {'name': 'status', 'type': 'text'},
        ],
    })
    pb.collection('users').create({'email': 'anna@example.com', 'password': 'anna-secret-123', 'passwordConfirm': 'anna-secret-123'})
    for invoice, customer in [('RE-1', "O'Brien"), ('RE-2', "O'Brien"), ('RE-3', 'Smith')]:
        pb.collection('orders').create({'invoice': invoice, 'customer': customer, 'status': 'open'})
```

<br/>
<br/>

It depends on tokeo-base and httpx. The guide [POCKETBASE.md](https://github.com/tokeo/tokeo/blob/master-2.x/tokeo-pocketbase/tokeo/core/pocketbase/POCKETBASE.md) explains logins and the silent guest, realtime, the times of the server with the hook file ```pb_hooks/realtime.pb.js```, pages of nicegui with live data, a backend that acts as its users, and the limits and options. Within a Tokeo app use the pocketbase extension ```tokeo.ext.pocketbase```, its handler ```app.db``` logs in a service identity before each request.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
