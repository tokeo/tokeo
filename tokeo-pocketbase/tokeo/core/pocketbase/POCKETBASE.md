# The ```pocketbase``` core library

```tokeo.core.pocketbase``` is a driver for the REST API of
[PocketBase](https://pocketbase.io) 0.40 and later, built on
[httpx](https://www.python-httpx.org): records, filters with safely quoted
values, logins, impersonation, batches, health and the realtime events of
changed records. Everything here is checked against PocketBase 0.40.6, its
binary and its source.

.. note::

    This page documents the library and how a Tokeo app uses it: the
    ```tokeo.ext.pocketbase``` extension gives the client of a service
    identity as ```app.db```, and ```app.nicegui.pb.live``` feeds pages of
    nicegui with realtime data. Both are only glue on top of this library.

## 1. What you get

| Part | Module | For |
|---|---|---|
| ```PocketBase``` | ```client``` | a client with its own connections and its own login |
| ```AuthKeeper``` | ```client``` | keeps a login valid and confirms it on the server |
| ```Collection``` | ```collection``` | the records of a collection: list, get, create, update, delete |
| ```Batch``` | ```batch``` | record changes in one transaction |
| ```Record```, ```ListResult``` | ```record``` | a record with its fields as attributes, a page of records |
| ```build_filter``` / ```pb.filter()```, ```path_segment``` | ```filter``` | values from outside quoted for a filter and for a path |
| ```TokeoPocketBaseError```, ```TokeoPocketBaseAuthError``` | ```exc``` | the errors |
| ```Realtime```, ```AsyncRealtime``` | ```realtime``` | the realtime events, in threads for plain functions or as tasks of asyncio, e.g. of nicegui |

All but the realtime channels are imported from ```tokeo.core.pocketbase```,
the channels from ```tokeo.core.pocketbase.realtime```.

## 2. Quick start

```python
from tokeo.core.pocketbase import PocketBase, TokeoPocketBaseError

with PocketBase('http://127.0.0.1:8090') as pb:
    pb.auth_with_password('users', 'anna@example.com', 'anna-secret-123')
    orders = pb.collection('orders')

    # search with placeholders, the values are quoted and escaped
    open_orders = orders.get_list(
        filter=pb.filter('customer = {:customer} && status = {:status}', {'customer': "O'Brien", 'status': 'open'}),
        sort='invoice',
    )
    for order in open_orders.items:
        orders.update(order.id, {'status': 'paid'})

    try:
        orders.get_one('missing00000001')
    except TokeoPocketBaseError as err:
        print(err.status)            # 404
```

A record is a dict whose fields read and write also as attributes,
```order.invoice```; an expanded relation is a record as well,
```order.expand.customer.email```.

## 3. Clients and logins

One ```PocketBase``` holds **one login**. Never log several identities in
on one client; take a client of its own for each.

| Way | Use |
|---|---|
| ```auth_with_password(collection, identity, password)``` | a service identity or a user with its password |
| ```auth_refresh()``` | renew the token; it also confirms on the server that the login holds |
| ```impersonate(collection, id)``` | a superuser gets a client that acts as a record, without its password |
| ```PocketBase(url, token=...)``` | a token from elsewhere, e.g. of a browser; refreshed on the collection it names |

### The silent guest

PocketBase takes a refused or expired token **without an error, as a
guest**. A list then comes back empty with status 200, a record answers 404,
a realtime channel gets no event. A token valid by its expiry may already
be refused: a changed password, a deleted record or a new secret of the
collection invalidates it at once.

So keep every login that runs for long by an ```AuthKeeper``` as
```before_request``` of its client. Before each request it renews a token
near its expiry and, every ```check_seconds```, asks the server by a refresh
whether the login still holds; a refused one is logged in anew, and a failed
new login raises instead of running on as a guest:

```python
from tokeo.core.pocketbase import AuthKeeper, PocketBase

pb = PocketBase('http://127.0.0.1:8090')

def login():
    pb.auth_with_password('users', 'service@example.com', 'secret')

pb.before_request = AuthKeeper(pb, login, min_valid_seconds=60, check_seconds=60)
```

A login of the client itself, e.g. a new impersonation, counts as
confirmed; ```pb.logins``` counts them, a refresh does not.

### Impersonation

A superuser acts as a user without knowing its password. The client of the
impersonation gets the rules of PocketBase for that user: its lists, records
and realtime events hold only what the user may see. The token of an
impersonation cannot be extended; a refresh still confirms it and answers
with the same token.

```python
anna = su.impersonate('users', anna_id, duration=3600)
mine = anna.collection('orders').get_full_list()
anna.close()
```

To keep such a login, renew it by a new impersonation into the same client:

```python
anna = su.impersonate('users', anna_id)
anna.before_request = AuthKeeper(anna, lambda: su.impersonate('users', anna_id, client=anna))
```

Within a Tokeo app ```app.db.impersonate(collection, id)``` does just this,
by the login of the service identity, which must be a superuser.

## 4. Records, filters and batches

```python
posts = pb.collection('posts')
page = posts.get_list(page=1, per_page=50, filter=pb.filter('views > {:n}', {'n': 10}), sort='-created', expand='owner')
everything = posts.get_full_list(per_page=500)
first = posts.get_first(pb.filter('title = {:t}', {'t': "it's"}))
post = posts.create({'title': 'Hello'}, files=[('doc', ('a.txt', b'hello', 'text/plain'))])
posts.update(post.id, {'title': 'more'}, files=[('doc+', ('b.txt', b'world', 'text/plain'))])
posts.delete(post.id)
```

- Build a filter from outside values only by ```pb.filter(expr, params)```;
  it escapes ```\``` and ```'``` as the filter parser of PocketBase reads them,
  so no value can leave its string
- Files go along with the fields as multipart, the fields as ```@jsonPayload```;
  a field ```name+``` appends a file
- A batch runs all changes in one transaction, all or nothing; switch the
  batch api on in the settings of PocketBase first, it is off by default

```python
created, upserted = pb.batch().create('posts', {'title': 'a'}).upsert('posts', {'id': 'post00000000001', 'title': 'b'}).send()
```

Every error raises ```TokeoPocketBaseError``` with ```status``` (0 without an
answer), ```url``` and ```data```, e.g. the invalid fields; a refused login
raises ```TokeoPocketBaseAuthError```.

## 5. Realtime

PocketBase sends the changes of records as server sent events. A channel
subscribes topics and calls a callback with each ```RealtimeEvent```, with
```action``` (create, update, delete) and ```record```.

```python
import time
from tokeo.core.pocketbase.realtime import Realtime

def on_order(event):
    print(event.action, event.record.invoice, event.record.status)

with Realtime(pb) as realtime:
    realtime.on_connect = reload_everything        # after each (re)connect
    realtime.subscribe('orders/*', on_order)       # all records
    realtime.subscribe(f'orders/{order_id}', on_one)   # one record
    realtime.subscribe('orders/*', on_open, {'query': {'filter': "status = 'open'", 'expand': 'customer'}})
    time.sleep(60)
```

Within asyncio, e.g. nicegui, ```AsyncRealtime``` runs as task and also
awaits coroutines:

```python
realtime = AsyncRealtime(pb).subscribe('orders/*', on_order)
task = asyncio.create_task(realtime.run())
...
await realtime.stop()
```

What a channel guarantees:

- **The events of the login only.** PocketBase checks the list and view
  rules of the collection with each event, for the login of the channel;
  a changed rule counts from the next event on
- **No events held by PocketBase for a slow callback.** A reader takes each
  event off the connection at once, the callbacks run apart from it. PocketBase
  keeps every event for a client that does not read, without any limit, and
  its timers do not end such a connection. So once ```max_pending``` events
  wait for the callbacks, the channel hangs up, PocketBase frees all it held,
  and the channel connects again after the callbacks caught up. Measured:
  3000 events of 20 KB with a hanging callback grew PocketBase by 52 MB, as
  much as without any realtime client; before, by 105 MB and on without end
- **No silent guest.** Before it sends its subscriptions and every
  ```auth_check``` seconds the channel confirms its login, by the
  ```before_request``` of the client or by a refresh. A login that cannot be
  confirmed shows as ```error``` with ```status``` connecting
- **Back after each loss.** A lost connection is opened again after a delay
  growing from 0.5 seconds up to ```max_delay```; one the server ended after it
  served at least ```STABLE_SECONDS``` (10) is opened again at once
- **Nothing left behind.** ```stop()``` ends the connection at once, also a
  read that waits; no thread, task or socket stays

What it does not:

- **Events between two connections are lost**, at most the time to connect;
  reload what you show in ```on_connect```
- **The order is not promised.** PocketBase sends each event on its own; it
  held in all measurements, but compare ```updated``` where order matters
- **A callback must be quick.** It runs in the dispatcher, one after the
  other; in asyncio it must not block the loop, run slow work by
  ```asyncio.to_thread``` in a task of its own

```status``` is live, connecting or stopped, ```error``` the last failure,
```topics``` the subscriptions with their options. A channel takes at most
1000 subscriptions, a topic with its options at most 2500 characters.

## 6. The times of the server and the hook file

PocketBase ends a realtime connection by itself:

- after **5 minutes without events**, to drop connections of clients that are
  gone, e.g. a crashed process, a sleeping laptop, a proxy that swallowed the
  close; the server only writes, it learns of a gone client only when a write
  fails
- after **30 minutes** in all, so no connection piles up resources and the
  garbage collector runs regularly; it also limits how long the login stored
  with the connection lives on, as an expiring token does not end it

Both are no setting of PocketBase, they are set by a hook of PocketBase in
JavaScript. ```realtime.pb.js.md``` next to this page holds the hook as code
only; copy it as ```pb_hooks/realtime.pb.js```:

.. include:: ./realtime.pb.js.md
   :code: javascript

- The times are **nanoseconds**, as all durations of PocketBase; ```120```
  alone would be 120 nanoseconds
- A constant outside the handler is not seen inside, the handler runs in a
  runtime of its own
- ```e.next()``` lets the connection go on, without it no client connects
- The values come from the environment of PocketBase, in seconds, else the
  defaults of the file, 120 and 600

PocketBase loads every ```*.pb.js``` of the dir ```pb_hooks``` **next to
```pb_data```**, not next to the binary; another dir is named by
```--hooksDir```. It restarts by itself when a file there changes; a changed
environment needs a restart:

```bash
mkdir -p /srv/pocketbase/pb_hooks && cp realtime.pb.js.md /srv/pocketbase/pb_hooks/realtime.pb.js
PB_REALTIME_IDLE_SECONDS=120 PB_REALTIME_MAX_SECONDS=600 ./pocketbase serve --dir /srv/pocketbase/pb_data
```

Sensible values:

| Use | idle | max | ```realtime_read_timeout``` of tokeo |
|---|---|---|---|
| PocketBase as shipped | 300 s | 1800 s | 330 s, the default |
| Recommended with tokeo, the defaults of the hook file | 120 s | 600 s | 150 s |
| The shortest sensible | 60 s | 300 s | 90 s |

- **The read timeout of a channel must stay above idle**, by about 30
  seconds; one below would hang up quiet but healthy connections
- **Each end of a connection reloads the pages of its channel** by
  ```on_connect```: a busy channel every max seconds, a quiet one every idle
  seconds, with 120 and 600 seconds 6 to 30 reloads per hour and page; a max
  below 300 seconds costs more in reloads than it wins
- **Idle below 60 seconds** gains little, many proxies end idle connections
  after 60 seconds anyway, e.g. nginx by its ```proxy_read_timeout```
- **Max** limits clients that do not confirm their login, e.g. browsers with
  the JavaScript SDK; the channels of tokeo-pocketbase confirm theirs anyway
- The times hold for **all clients** of the server

Check it: ```time curl -sN http://127.0.0.1:8090/api/realtime > /dev/null```
ends after idle, 120 seconds with the defaults of the hook file.

## 7. Within a Tokeo app

The extension ```tokeo.ext.pocketbase``` gives ```app.db```, a handler with
the client of a service identity as ```app.db.pb```:

```yaml
pocketbase:
  url: https://pb.example.com
  auth_identity: service@example.com
  auth_password: !vault:master ...
  # renew the token once fewer seconds remain
  auth_token_min_valid_seconds: 60
  # confirm the login on the server after these seconds
  auth_check_seconds: 60
  # concurrent http connections of the process
  max_connections: null
  # realtime channels, e.g. of app.nicegui.pb.live
  realtime_read_timeout: 330
  realtime_max_pending: 1000
  realtime_auth_check: 60
  realtime_max_delay: 30
```

- The service identity logs in on first use and is kept by an
  ```AuthKeeper```: a changed password or a deleted service identity raises
  within ```auth_check_seconds``` instead of running on as a guest
- ```app.db.get_list('orders', filter=...)``` and the other CRUD methods are
  short forms of ```app.db.collection('orders')```
- ```app.db.impersonate('users', user_id)``` gives a client acting as the
  user, its login kept by a new impersonation; the service identity must
  be a superuser
- ```app.db.realtime_options``` are the ```realtime_*``` config as options
  of a channel: ```Realtime(app.db.pb, **app.db.realtime_options)```

## 8. Pages of nicegui with live data

```app.nicegui.pb.live``` feeds pages with the realtime data of PocketBase. A
**feed** holds the client of one login and its channels; a page follows
topics on a feed and loads what it shows by the client of the feed.

It is there when the app loads both extensions, ```tokeo.ext.pocketbase``` and
```tokeo.ext.nicegui```, in any order: the pocketbase extension adds
```app.nicegui.pb``` by the hook ```tokeo_nicegui_extend``` of the nicegui
extension; without nicegui that hook is not defined and nothing of nicegui
is loaded. The code is ```tokeo.ext.pocketbase.live```.

### A dashboard for everybody, by the service identity

```python
import asyncio
from nicegui import ui


async def orders_view(feed, filter=None):
    # the orders the feed lets the page see, with the badge of its state
    orders = feed.pb.collection('orders')
    table = ui.table(columns=COLUMNS, rows=[], row_key='id')
    feed.badge()

    async def reload():
        records = await asyncio.to_thread(orders.get_full_list, filter=filter, sort='invoice')
        table.rows = [dict(record) for record in records]
        table.update()

    def on_order(event):
        rows = [row for row in table.rows if row['id'] != event.record.id]
        if event.action != 'delete':
            rows.append(dict(event.record))
        table.rows = sorted(rows, key=lambda row: row['invoice'])
        table.update()

    await feed.follow('orders/*', on_order, reload, filter=filter)


@ui.page('/dashboard')
async def dashboard():
    await orders_view(app.nicegui.pb.live.service())
```

All dashboards of the process share one channel.

### A page as the user, separated at the source

```python
@ui.page('/my-orders')
async def my_orders():
    user_id = app.storage.user['id']             # from the login of your site
    await orders_view(await app.nicegui.pb.live.user('users', user_id))
```

The feed acts as the user by an impersonation: PocketBase applies its rules
to the user in the lists and in the events, the page never gets what the
user may not see. The pages of one user share one feed and one channel; it
closes with its client after the last page of the user left.

### Pages split by a filter, on the service identity

```python
@ui.page('/team/{team}')
async def team_orders(team: str):
    feed = app.nicegui.pb.live.service()
    await orders_view(feed, filter=feed.pb.filter('team = {:team}', {'team': team}))
```

PocketBase checks the filter with each event; all such pages share the
channels of the service identity, one subscription per filter, another
channel beyond 1000. It separates by the filter, not by the rules: take a
page as the user where the separation must be safe.

### How a page lives

- ```follow()``` subscribes, then loads by ```reload```: at once on a live
  channel, else once the channel connects; after each reconnect again
- A page holds its feed until nicegui deletes it, ```reconnect_timeout```
  after its tab closed (5 seconds by default); then all it followed is
  unsubscribed, a channel without subscriptions stops, a user feed without
  pages closes with its client. A dead tab is served no longer
- ```on_event``` runs in the event loop and must be quick, reload more in a
  task of its own
- ```feed.badge(labels={'live': 'aktuell'})``` shows live, connecting,
  offline with the error as tooltip, or off; the labels are free

### Cost

| Pages | Connections to PocketBase |
|---|---|
| dashboards | one per process, for any number of tabs |
| pages as the user | one per user with open tabs, not per tab |
| pages split by a filter | the ones of the service identity, one more per 1000 filters |

An idle connection costs PocketBase some 20 to 40 KB; each event is checked
against the rule for each subscription of each channel, so fewer channels
mean less work. Many users online at once cost a connection each as users,
the filter pages are cheaper but not safe by the rules.

## 9. A backend that forwards as the users

A backend that serves several users, e.g. an api in front of PocketBase,
acts for each request as its user, so PocketBase applies its rules:

```python
import asyncio
from fastapi import Depends


@app.nicegui.fastapi_app.get('/api/my-orders')
async def my_orders(user_id: str = Depends(current_user)):     # your own login
    def load():
        with app.db.impersonate('users', user_id) as pb:
            return [dict(record) for record in pb.collection('orders').get_full_list()]

    return await asyncio.to_thread(load)
```

- Each request costs one impersonation more; the client of the request is
  closed with the ```with```
- ```current_user``` comes from the login of your backend, never from a
  parameter of the request; the user id decides what PocketBase shows
- A worker that follows the changes of one user runs a channel on such a
  client: ```Realtime(app.db.impersonate('users', user_id), **app.db.realtime_options)```;
  stop it and close its client when done
- The service identity must be a superuser; keep its password in the vault

## 10. Limits and options

Measured against PocketBase 0.40.6:

| What | Value | Changed by |
|---|---|---|
| A realtime connection without events ends after | 300 s | the hook file, section 6 |
| Any realtime connection ends after | 30 min | the hook file, section 6 |
| Subscriptions per connection | 1000 | not changeable |
| Length of a topic with its options | 2500 characters | not changeable |
| The subscriptions must come from | the ip of the connection | not changeable |
| Change of the login on a connection | only from guest to login, else 403 | not changeable |
| A refused or expired token | is taken as a guest, no error | not changeable |
| Token lifetime of users / _superusers | 5 days / 1 day | each auth collection |
| Rate limits | off; when on, per ip e.g. 2 logins in 3 s, 20 creates in 5 s, 300 requests in 10 s; superusers are exempt | settings |
| Batch api | off; when on 50 requests, 3 s, about 128 MB | settings |
| A text field with ```max``` 0 | at most 5000 characters | each field |

The options of a channel, within a Tokeo app the ```realtime_*``` config:

| Option | Config of Tokeo | Default | Meaning |
|---|---|---|---|
| ```max_pending``` | ```realtime_max_pending``` | 1000 | events that may wait for the callbacks before the channel hangs up |
| ```auth_check``` | ```realtime_auth_check``` | 60 s | seconds between two confirmations of the login |
| ```read_timeout``` | ```realtime_read_timeout``` | 330 s | a connection silent longer counts as lost, 30 s above idle |
| ```max_delay``` | ```realtime_max_delay``` | 30 s | the longest delay before a new try, doubled from 0.5 s; also the pause after the callbacks fell behind |

## 11. Tips for running it

- **Rate limits** count per ip. Services behind one address share them; a
  service logging in often can hit ```*:auth``` with 2 logins in 3 seconds.
  A superuser is exempt
- **Behind a proxy** turn the buffering of the realtime path off and allow
  the read timeout of your idle option; PocketBase sends
  ```X-Accel-Buffering: no``` for nginx. The subscriptions must come from the
  ip of the connection, so all requests of one service leave by one address
- **Reload in ```on_connect```**, events between two connections are lost
- **Compare ```updated```** where the order of events matters
- **The user id of a page or request** comes from your own login, never from
  the request
- **Impersonation** needs a superuser as service identity
- **Tests against the binary**: the live tests of tokeo start PocketBase
  from ```TOKEO_TEST_POCKETBASE_BIN``` on a free port and skip without it
