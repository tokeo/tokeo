"""
Tests for tokeo.ext.pocketbase.live against a real PocketBase server, with
nicegui in its user simulation: the three kinds of pages, a dashboard by the
service identity, a page of a user by an impersonation and a page split by a
filter; the badge, the channels a feed opens and closes, pages that leave
nothing behind, and a user whose login is lost while the page is open.

The binary named by ```TOKEO_TEST_POCKETBASE_BIN``` starts once for the
module on a free port with the superuser as service identity, the users
anna and bert and the collection orders, whose rules let a user see only
its own orders; without the binary the tests are skipped. Each test starts
with no order and runs the app by ```NiceguiSimulation``` of
```tests.utils.nicegui```.
"""

import os
import asyncio
import subprocess

import pytest
from cement.utils.misc import init_defaults
from nicegui import ui
from tokeo.main import TokeoTest
from tokeo.ext.nicegui import TokeoNiceguiError
from tokeo.core.pocketbase import TokeoPocketBaseClient, TokeoPocketBaseError
from tests.utils import find_free_port, wait_until_async
from tests.utils.process import SubProcess
from tests.utils.nicegui import NiceguiSimulation

#: The pocketbase binary of the tests, e.g. of version 0.40.6
POCKETBASE_BIN = os.environ.get('TOKEO_TEST_POCKETBASE_BIN', '')

pytestmark = pytest.mark.skipif(
    not os.path.isfile(POCKETBASE_BIN),
    reason='needs the pocketbase binary (set TOKEO_TEST_POCKETBASE_BIN)',
)

SUPERUSER = ('admin@tokeo.test', 'admin-pass-123')

# the rule of orders: a user sees its own orders only
OWN = 'owner = @request.auth.id'


class LiveTest(TokeoTest):
    """A test app with pocketbase and nicegui, the superuser as service identity."""

    class Meta:
        extensions = ['tokeo.ext.print', 'tokeo.ext.pocketbase', 'tokeo.ext.nicegui']

    #: The app of the running test, read by the pages
    running = None

    def __init__(self, url, **pocketbase):
        config = init_defaults('pocketbase', 'nicegui')
        config['pocketbase'].update(url=url, auth_identity=SUPERUSER[0], auth_password=SUPERUSER[1], **pocketbase)
        config['nicegui'].update(routes=f'{__name__}:_map_pages', binding_refresh_interval=0.05)
        super().__init__(argv=[], config_defaults=config)
        LiveTest.running = self


async def _show_orders_page(feed, filter=None):
    # the orders the feed lets the page see, as text, with the badge
    orders = feed.pb.collection('orders')
    titles = {}
    label = ui.label()
    feed.show_badge()

    def show():
        label.text = f'orders: [{",".join(sorted(titles.values()))}]'

    async def reload():
        records = await asyncio.to_thread(orders.get_full_list, filter=filter)
        titles.clear()
        titles.update({record.id: record.title for record in records})
        show()

    def on_order(event):
        if event.action == 'delete':
            titles.pop(event.record.id, None)
        else:
            titles[event.record.id] = event.record.title
        show()

    await feed.follow('orders/*', on_order, reload, filter=filter)


def _map_pages():
    # the route handler: a dashboard, a page of a user and a page split by filter
    live = LiveTest.running.nicegui.pb.live

    @ui.page('/dashboard')
    async def dashboard():
        await _show_orders_page(live.get_service_feed())

    @ui.page('/mine/{user_id}')
    async def mine(user_id: str):
        await _show_orders_page(await live.get_user_feed('users', user_id))

    @ui.page('/split/{user_id}')
    async def split(user_id: str):
        feed = live.get_service_feed()
        await _show_orders_page(feed, filter=feed.pb.filter('owner = {:id}', {'id': user_id}))


def _is_healthy(url):
    # whether the server answers its health check
    try:
        with TokeoPocketBaseClient(url) as pb:
            return pb.check_health()['code'] == 200
    except TokeoPocketBaseError:
        return False


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    """A running PocketBase with the superuser, anna, bert and orders."""
    data = str(tmp_path_factory.mktemp('pocketbase'))
    subprocess.run([POCKETBASE_BIN, 'superuser', 'upsert', *SUPERUSER, '--dir', data], check=True, capture_output=True)
    url = f'http://127.0.0.1:{find_free_port()}'
    with SubProcess.run(POCKETBASE_BIN, 'serve', '--dir', data, '--http', url.removeprefix('http://'), timeout=15) as process:
        process.wait_for(lambda: _is_healthy(url))
        with TokeoPocketBaseClient(url) as su:
            su.auth_with_password('_superusers', *SUPERUSER)
            su.send(
                'POST',
                '/api/collections',
                json={
                    'name': 'orders',
                    'type': 'base',
                    'listRule': OWN,
                    'viewRule': OWN,
                    'fields': [
                        {'name': 'title', 'type': 'text'},
                        {'name': 'owner', 'type': 'relation', 'collectionId': '_pb_users_auth_', 'maxSelect': 1},
                    ],
                },
            )
            users = {}
            for name in ('anna', 'bert'):
                users[name] = (
                    su.collection('users')
                    .create({'email': f'{name}@tokeo.test', 'password': 'user-pass-123', 'passwordConfirm': 'user-pass-123'})
                    .id
                )
        yield dict(url=url, **users)


@pytest.fixture
def su(server):
    """A client of the superuser, all orders deleted after the test."""
    with TokeoPocketBaseClient(server['url']) as client:
        client.auth_with_password('_superusers', *SUPERUSER)
        yield client
        for order in client.collection('orders').get_full_list():
            client.collection('orders').delete(order.id)


def _create_order(su, title, owner):
    # create an order of the owner by the superuser
    return su.collection('orders').create({'title': title, 'owner': owner})


async def _wait_for_text(user, text):
    # wait until the page shows the text, a realtime event may take a moment
    await user.should_see(text, retries=50)


# service


def test_live_dashboard_shows_all_orders_and_follows_their_changes(server, su):
    """The dashboard loads A-1, then follows a create, change and delete."""
    a1 = _create_order(su, 'A-1', server['anna'])
    with LiveTest(server['url']) as app:

        async def visit(simulation):
            user = simulation.user()
            await user.open('/dashboard')
            await _wait_for_text(user, 'orders: [A-1]')
            await _wait_for_text(user, 'live')
            b1 = await asyncio.to_thread(_create_order, su, 'B-1', server['bert'])
            await _wait_for_text(user, 'orders: [A-1,B-1]')
            await asyncio.to_thread(su.collection('orders').update, a1.id, {'title': 'A-1x'})
            await asyncio.to_thread(su.collection('orders').delete, b1.id)
            await _wait_for_text(user, 'orders: [A-1x]')

        NiceguiSimulation(app).run(visit)


def test_live_pages_share_a_channel_and_leave_nothing_behind(server, su):
    """Two dashboards share a channel, stopped once both pages are gone."""
    with LiveTest(server['url']) as app:

        async def visit(simulation):
            first, second = simulation.user(), simulation.user()
            await first.open('/dashboard')
            await second.open('/dashboard')
            feed = app.nicegui.pb.live.get_service_feed()
            assert await wait_until_async(lambda: feed.state == 'live')
            channel = feed.channels[0]
            assert (len(feed.channels), channel.topics) == (1, ['orders/*'])
            first.client.delete()
            assert (feed.channels, channel.status) == ([channel], 'live')
            second.client.delete()
            assert await wait_until_async(lambda: channel.status == 'stopped')
            assert (feed.channels, feed.state) == ([], 'stopped')

        NiceguiSimulation(app).run(visit)


# user


def test_live_user_page_gets_only_what_the_rules_let_the_user_see(server, su):
    """anna's page loads A-1 but not B-1, and of B-2 and A-2 it gets A-2 only."""
    _create_order(su, 'A-1', server['anna'])
    _create_order(su, 'B-1', server['bert'])
    with LiveTest(server['url']) as app:

        async def visit(simulation):
            user = simulation.user()
            await user.open(f'/mine/{server["anna"]}')
            await _wait_for_text(user, 'orders: [A-1]')
            await asyncio.to_thread(_create_order, su, 'B-2', server['bert'])
            await asyncio.to_thread(_create_order, su, 'A-2', server['anna'])
            await _wait_for_text(user, 'orders: [A-1,A-2]')

        NiceguiSimulation(app).run(visit)


def test_live_user_feed_is_shared_by_the_pages_of_the_user_and_closed_after_the_last(server, su):
    """anna's pages share a feed; after both left it closes with its client."""
    with LiveTest(server['url']) as app:

        async def visit(simulation):
            first, second = simulation.user(), simulation.user()
            await first.open(f'/mine/{server["anna"]}')
            await second.open(f'/mine/{server["anna"]}')
            feeds = list(app.nicegui.pb.live._users.values())
            assert len(feeds) == 1
            feed = feeds[0]
            assert await wait_until_async(lambda: feed.state == 'live')
            first.client.delete()
            assert not feed.is_closed
            second.client.delete()
            assert await wait_until_async(lambda: feed.is_closed and feed.pb.http_client.is_closed)
            assert (app.nicegui.pb.live._users, feed.channels) == ({}, [])

        NiceguiSimulation(app).run(visit)


def test_live_user_page_shows_offline_once_the_user_is_deleted(server, su):
    """
    The login of a user deleted while its page is open is checked every
    0.2 seconds; the new impersonation fails, so the badge turns offline
    with the error instead of running on as a silent guest.
    """
    gone = su.collection('users').create({'email': 'gone@tokeo.test', 'password': 'user-pass-123', 'passwordConfirm': 'user-pass-123'})
    with LiveTest(server['url'], auth_check_seconds=0.2, realtime_auth_check_seconds=0.2, realtime_max_delay_seconds=0.2) as app:

        async def visit(simulation):
            user = simulation.user()
            await user.open(f'/mine/{gone.id}')
            await _wait_for_text(user, 'live')
            await asyncio.to_thread(su.collection('users').delete, gone.id)
            await _wait_for_text(user, 'offline')
            feed = next(iter(app.nicegui.pb.live._users.values()))
            assert feed.state == 'offline' and '404' in feed.error

        NiceguiSimulation(app).run(visit)


# filter


def test_live_split_page_gets_only_the_orders_of_its_filter(server, su):
    """The page split for anna loads A-1, and of B-2 and A-2 it gets A-2 only."""
    _create_order(su, 'A-1', server['anna'])
    _create_order(su, 'B-1', server['bert'])
    with LiveTest(server['url']) as app:

        async def visit(simulation):
            user = simulation.user()
            await user.open(f'/split/{server["anna"]}')
            await _wait_for_text(user, 'orders: [A-1]')
            await asyncio.to_thread(_create_order, su, 'B-2', server['bert'])
            await asyncio.to_thread(_create_order, su, 'A-2', server['anna'])
            await _wait_for_text(user, 'orders: [A-1,A-2]')

        NiceguiSimulation(app).run(visit)


def test_live_feed_opens_another_channel_beyond_max_subscriptions(server, su):
    """One subscription per channel: anna and bert get a channel each."""
    with LiveTest(server['url']) as app:

        async def visit(simulation):
            feed = app.nicegui.pb.live.get_service_feed()
            feed.max_subscriptions = 1
            anna, bert = simulation.user(), simulation.user()
            await anna.open(f'/split/{server["anna"]}')
            await bert.open(f'/split/{server["bert"]}')
            assert len(feed.channels) == 2
            assert await wait_until_async(lambda: feed.state == 'live')
            await asyncio.to_thread(_create_order, su, 'B-1', server['bert'])
            await _wait_for_text(bert, 'orders: [B-1]')
            await anna.should_see('orders: []')

        NiceguiSimulation(app).run(visit)


# outside a page


def test_live_follow_show_badge_and_get_user_feed_refuse_to_run_outside_a_page(server):
    """follow, get_user_feed and show_badge raise outside of a page of nicegui."""
    with LiveTest(server['url']) as app:

        async def visit(simulation):
            feed = app.nicegui.pb.live.get_service_feed()
            with pytest.raises(TokeoNiceguiError):
                await feed.follow('orders/*', print)
            with pytest.raises(TokeoNiceguiError):
                await app.nicegui.pb.live.get_user_feed('users', server['anna'])
            with pytest.raises(TokeoNiceguiError):
                feed.show_badge()

        NiceguiSimulation(app).run(visit)
