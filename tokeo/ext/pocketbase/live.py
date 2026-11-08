"""
The realtime data of PocketBase for the pages of nicegui, reached as
```app.nicegui.pb.live```. The pocketbase extension adds it to the nicegui
extension by the hook ```tokeo_nicegui_extend```, only when both are loaded.
A feed holds the client of one login and its realtime channels; pages follow
topics on it and load what they show by its client. A page holds a feed
until nicegui deletes the page, then all it followed is unsubscribed;
nothing outlives the pages.

### Example

```python
@ui.page('/orders')
async def orders_page():
    feed = app.nicegui.pb.live.get_service_feed()
    orders = feed.pb.collection('orders')
    table = ui.table(columns=COLUMNS, rows=[], row_key='id')
    feed.show_badge()

    async def reload():
        records = await asyncio.to_thread(orders.get_full_list)
        table.rows = [dict(record) for record in records]
        table.update()

    def on_order(event):
        ...

    await feed.follow('orders/*', on_order, reload)
```

### Notes

: Three kinds of pages:

    - ```get_service_feed()```: one feed by the service identity of
        ```app.db```, shared by all pages, e.g. for dashboards everybody sees
        alike
    - ```await get_user_feed(collection, id)```: a feed of a user by an
        impersonation of the service identity; PocketBase applies its rules to
        the user, so the data is separated at the source, in the records and
        the events
    - ```follow(..., filter=...)``` on the service feed: each page gets only
        the events whose records match its filter, checked by PocketBase; it
        separates by the filter, not by the rules, so take
        ```get_user_feed()``` where the separation must be safe

: A feed opens a channel for up to ```max_subscriptions``` subscriptions,
    PocketBase takes no more on one connection, and another one beyond;
    a channel without subscriptions is stopped, a user feed without pages
    is closed with its client

: ```reload``` runs once the subscription is active and after each
    reconnect of its channel, so a page misses no change; ```on_event```
    runs in the event loop and must be quick, load more by
    ```asyncio.to_thread``` in a task of its own

"""

import inspect
import asyncio
import logging
from collections import namedtuple
from nicegui import ui, app as nicegui_app, background_tasks, context
from tokeo.ext.nicegui import check_user_context
from tokeo.core.pocketbase import TokeoPocketBaseError
from tokeo.core.pocketbase.realtime import MAX_SUBSCRIPTIONS, AsyncRealtime, format_topic_key

#: The logger of this module
LOG = logging.getLogger(__name__)

#: The states of a feed: live while all its channels are, connecting while
#: one tries, offline while one tries after an error, stopped without channel
LIVE_STATES = ('live', 'connecting', 'offline', 'stopped')

#: The texts of the badge for each state
LIVE_LABELS = dict(live='live', connecting='connecting', offline='offline', stopped='off')

#: The colors of the badge for each state
LIVE_COLORS = dict(live='positive', connecting='warning', offline='negative', stopped='grey')

# one topic a page follows on a channel of a feed
_Follower = namedtuple('_Follower', 'page channel topic options on_event reload')


async def _call(function, *args):
    # call a function or coroutine of a page, a failure is logged
    try:
        result = function(*args)
        if inspect.isawaitable(result):
            await result
    except Exception:
        LOG.exception('tokeo live: %r failed', function)


class LiveFeed:
    """
    The realtime events of one login for the pages, with its client.

    ### Args

    - **pb** (TokeoPocketBaseClient): The client of the login, for the requests
        of the pages and the realtime channels

    ### Keyword Args

    - **max_subscriptions** (int): The subscriptions of one channel, another
        channel opens beyond
    - **has_own_client** (bool): Close the client with the feed
    - **channel_options**: Options of each ```AsyncRealtime```, e.g.
        ```max_pending``` or ```auth_check_seconds```

    """

    def __init__(self, pb, max_subscriptions=MAX_SUBSCRIPTIONS, has_own_client=False, **channel_options):
        #: The client of the login, for the requests of the pages
        self.pb = pb
        #: The subscriptions of one channel
        self.max_subscriptions = max_subscriptions
        #: Close the client with the feed
        self.has_own_client = has_own_client
        #: Options of each channel
        self.channel_options = channel_options
        #: The realtime channels of the feed
        self.channels = []
        #: Called without arguments once the last page left
        self.on_idle = None
        #: True once closed
        self.is_closed = False
        self._followers = []
        self._pages = set()

    @property
    def state(self):
        """The state of the feed, one of ```LIVE_STATES```."""
        if not self.channels:
            return 'stopped'
        if all(channel.status == 'live' for channel in self.channels):
            return 'live'
        return 'offline' if self.error else 'connecting'

    @property
    def error(self):
        """The error of a channel that is not live, None without."""
        return next((channel.error for channel in self.channels if channel.status != 'live' and channel.error), None)

    def hold(self, page):
        """
        Keep the feed while a page lives.

        ### Args

        - **page** (Client): The client of nicegui of the page

        ### Notes

        : Once nicegui deletes the page all it followed is unsubscribed;
            after the last page ```on_idle``` is called

        """
        if page.id in self._pages:
            return
        self._pages.add(page.id)

        def release():
            # the page is gone: unsubscribe what it followed, stop an empty channel
            self._pages.discard(page.id)
            for follower in [follower for follower in self._followers if follower.page == page.id]:
                self._followers.remove(follower)
                follower.channel.unsubscribe(follower.topic, follower.on_event, follower.options)
                if not follower.channel.topics and follower.channel in self.channels:
                    self.channels.remove(follower.channel)
                    background_tasks.create(follower.channel.stop(), name='tokeo live channel stop')
            if not self._pages and self.on_idle:
                self.on_idle()

        page.on_delete(release)

    async def follow(self, topic, on_event, reload=None, filter=None, options=None):
        """
        Follow the changes of a topic on the current page while it lives.

        ### Args

        - **topic** (str): ```<collection>/*``` or ```<collection>/<id>```
        - **on_event** (callable): Called with each ```RealtimeEvent```, a
            function or coroutine
        - **reload** (callable): Loads what the page shows, a function or
            coroutine; runs once the subscription is active and after each
            reconnect
        - **filter** (str): A filter of PocketBase the records of the events
            must match, built by ```pb.filter()``` from outside values
        - **options** (dict): Further options of the subscription, e.g.
            ```{'query': {'expand': 'owner'}}```

        ### Raises

        - **TokeoNiceguiError**: If called outside of a page
        - **TokeoPocketBaseError**: If the feed is closed

        """
        check_user_context()
        if self.is_closed:
            raise TokeoPocketBaseError('tokeo live: the feed is closed')
        page = context.client
        self.hold(page)
        if filter:
            options = dict(options or {})
            options['query'] = {**options.get('query', {}), 'filter': filter}
        key = format_topic_key(topic, options)
        # the channel that has the subscription, else one with room, else a new one
        channel = next((channel for channel in self.channels if key in channel.topics), None)
        channel = channel or next((channel for channel in self.channels if len(channel.topics) < self.max_subscriptions), None)
        if channel is None:
            channel = AsyncRealtime(self.pb, **self.channel_options)

            async def reload_pages(channel=channel):
                # events may be missing after a (re)connect: its pages load anew
                await asyncio.gather(*(_call(f.reload) for f in self._followers if f.channel is channel and f.reload))

            channel.on_connect = reload_pages
            self.channels.append(channel)
            background_tasks.create(channel.run(), name='tokeo live channel')
        channel.subscribe(topic, on_event, options)
        self._followers.append(_Follower(page.id, channel, topic, options, on_event, reload))
        # a new channel reloads the page on its connect, a live one now
        if reload and channel.connected_event.is_set():
            await channel.flush()
            await _call(reload)

    def show_badge(self, labels=None):
        """
        Show the state of the feed on the current page.

        ### Args

        - **labels** (dict): Texts of the states, e.g.
            ```{'live': 'aktuell'}```, the others stay

        ### Returns

        - **Element**: The badge, one of ```LIVE_STATES``` visible at a time;
            an offline one shows the error as tooltip

        ### Raises

        - **TokeoNiceguiError**: If called outside of a page

        ### Notes

        : nicegui reads ```state``` every ```binding_refresh_interval```

        """
        check_user_context()
        labels = {**LIVE_LABELS, **(labels or {})}
        with ui.element('span') as box:
            for state in LIVE_STATES:
                badge = ui.badge(labels[state], color=LIVE_COLORS[state]).bind_visibility_from(self, 'state', value=state)
                if state == 'offline':
                    with badge:
                        ui.tooltip().bind_text_from(self, 'error', backward=lambda error: error or '')
        return box

    async def close(self):
        """Stop the channels of the feed, close its client when it owns it."""
        self.is_closed = True
        channels, self.channels = self.channels, []
        self._followers.clear()
        await asyncio.gather(*(channel.stop() for channel in channels))
        if self.has_own_client:
            self.pb.close()


class LiveHub:
    """
    The live data of PocketBase for the pages, by ```app.db```.

    ### Args

    - **app** (Application): The Cement application with ```app.db``` of
        the pocketbase extension

    ### Keyword Args

    - **channel_options**: Options of each ```AsyncRealtime```, over the
        ```realtime_*``` options of the pocketbase config

    ### Notes

    : Reached as ```app.nicegui.pb.live```, made on first use; all feeds
        close when nicegui shuts down

    """

    def __init__(self, app, **channel_options):
        #: The Cement application
        self.app = app
        #: Options of each channel
        self.channel_options = {**app.db.realtime_options, **channel_options}
        self._service = None
        self._users = {}
        self._lock = asyncio.Lock()
        nicegui_app.on_shutdown(self.close)

    def get_service_feed(self):
        """
        Get the feed of the service identity, shared by all pages.

        ### Returns

        - **LiveFeed**: The feed by ```app.db.pb```

        ### Notes

        : For dashboards everybody sees alike, and for pages that follow
            by a filter what belongs to their user

        """
        if self._service is None or self._service.is_closed:
            self._service = LiveFeed(self.app.db.pb, **self.channel_options)
        return self._service

    async def get_user_feed(self, collection, record_id):
        """
        Get the feed of a user, held by the current page.

        ### Args

        - **collection** (str): The auth collection of the user, e.g.
            ```users```
        - **record_id** (str): The id of the user

        ### Returns

        - **LiveFeed**: The feed by an impersonation of the user; its
            client loads only what the rules of PocketBase let the user see

        ### Raises

        - **TokeoPocketBaseAuthError**: If the service identity is no
            superuser or the user is unknown

        ### Notes

        : Shared by the pages of the user, closed with its client after the
            last one left

        : The site must take ```record_id``` from its own login of the
            visitor, never from the request

        """
        check_user_context()
        page = context.client
        key = (collection, record_id)
        async with self._lock:
            feed = self._users.get(key)
            if feed is None:
                pb = await asyncio.to_thread(self.app.db.impersonate, collection, record_id)
                feed = LiveFeed(pb, has_own_client=True, **self.channel_options)

                def close_idle_feed(feed=feed):
                    # the last page of the user left: free channels and client
                    if self._users.get(key) is feed:
                        del self._users[key]
                    background_tasks.create(feed.close(), name='tokeo live feed close')

                feed.on_idle = close_idle_feed
                self._users[key] = feed
            feed.hold(page)
        return feed

    async def close(self):
        """Close all feeds, called when nicegui shuts down."""
        feeds = [feed for feed in (self._service, *self._users.values()) if feed is not None]
        self._service, self._users = None, {}
        await asyncio.gather(*(feed.close() for feed in feeds))


class NiceguiPocketBase:
    """
    The PocketBase part of the nicegui extension, as ```app.nicegui.pb```.

    ### Args

    - **app** (Application): The Cement application with ```app.db```

    ### Notes

    : Added by the pocketbase extension through the hook
        ```tokeo_nicegui_extend``` of the nicegui extension

    """

    def __init__(self, app):
        #: The Cement application
        self.app = app
        self._live = None

    @property
    def live(self):
        """(LiveHub) The realtime data of PocketBase for pages, made on first use"""
        if self._live is None:
            self._live = LiveHub(self.app)
        return self._live
