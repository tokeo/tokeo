# Tokeo PB

The [PocketBase](https://pocketbase.io) driver of [Tokeo](https://github.com/tokeo/tokeo) for the REST API of PocketBase 0.40 and later, built on [httpx](https://www.python-httpx.org), like records to search, read and write, filters with safely quoted values, login, refresh and impersonation, batches in one transaction and the health check.

<br/>

## Features

- Records to list, page through, find, get, create, update and delete, with ```expand```, ```fields``` and ```sort```
- Files uploaded along with the fields of a record
- Filters with placeholders, each value quoted and escaped for the filter syntax of PocketBase
- Login with password, token refresh and impersonation of a record by a superuser
- A hook before each request, e.g. to log in on first use
- Batches of record changes in one transaction, all or nothing, once the batch api is switched on in PocketBase
- Each client with its own connections and its own login
- Records as dicts that read and write their fields also as attributes
- Errors with the status, the url and the invalid fields of the answer

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

It depends on tokeo-base and httpx. Within a Tokeo app use the pocketbase extension ```tokeo.ext.pocketbase```, its handler ```app.db``` logs in a service identity before each request.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
