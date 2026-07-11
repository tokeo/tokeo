"""
Ported from midi-smtp-server test/specs/ports_and_hosts_test.rb.

Checks the ports/hosts parsing and the resulting bind addresses for three
scenarios, each asserting ports, hosts and addresses separately (as midi does).
"""

from tokeo.core.smtpd.server import SmtpdServer
from tests.core.smtpd.lib.capture_smtpd_events import CaptureSmtpdEvents


def _smtpd(ports, hosts):
    return SmtpdServer(CaptureSmtpdEvents(), ports=ports, hosts=hosts)


# --- 1 port, 2 hosts ---------------------------------------------------------


def test_1_port_2_hosts_ports():
    assert _smtpd(2525, '127.0.0.1, ::1').ports == ['2525']


def test_1_port_2_hosts_hosts():
    assert _smtpd(2525, '127.0.0.1, ::1').hosts == ['127.0.0.1', '::1']


def test_1_port_2_hosts_addresses():
    assert _smtpd(2525, '127.0.0.1, ::1').addresses == ['127.0.0.1:2525', '::1:2525']


# --- 2 ports, 2 hosts --------------------------------------------------------


def test_2_ports_2_hosts_ports():
    assert _smtpd('2525, 3535', '127.0.0.1, ::1').ports == ['2525', '3535']


def test_2_ports_2_hosts_hosts():
    assert _smtpd('2525, 3535', '127.0.0.1, ::1').hosts == ['127.0.0.1', '::1']


def test_2_ports_2_hosts_addresses():
    assert _smtpd('2525, 3535', '127.0.0.1, ::1').addresses == ['127.0.0.1:2525', '::1:3535']


# --- 3 ports (a range), 2 hosts ---------------------------------------------


def test_3_ports_2_hosts_ports():
    assert _smtpd('2525, 2525:3535', '127.0.0.1, ::1').ports == ['2525', '2525:3535']


def test_3_ports_2_hosts_hosts():
    assert _smtpd('2525, 2525:3535', '127.0.0.1, ::1').hosts == ['127.0.0.1', '::1']


def test_3_ports_2_hosts_addresses():
    assert _smtpd('2525, 2525:3535', '127.0.0.1, ::1').addresses == ['127.0.0.1:2525', '::1:2525', '::1:3535']
