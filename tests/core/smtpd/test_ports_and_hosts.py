"""
Ported from the reference suite's test/specs/ports_and_hosts_test.rb.

Checks the ports/hosts parsing and the resulting bind addresses for three
scenarios, each asserting ports, hosts and addresses separately.
"""

# test rules exception: numbered test names mirror the midi-smtp-server suite

from tokeo.core.smtpd.server import SmtpdServer
from tests.core.smtpd.lib.smtpd_tests_capture_events_helper import CaptureSmtpdEvents


def _smtpd(ports, hosts):
    return SmtpdServer(CaptureSmtpdEvents(), ports=ports, hosts=hosts)


# ---- 1 port, 2 hosts --------------------------------------------------------


def test_1_port_2_hosts_ports():
    """Port 2525 with two hosts gives the ports ["2525"]."""
    assert _smtpd(2525, '127.0.0.1, ::1').ports == ['2525']


def test_1_port_2_hosts_hosts():
    """ "127.0.0.1, ::1" gives both hosts without blanks."""
    assert _smtpd(2525, '127.0.0.1, ::1').hosts == ['127.0.0.1', '::1']


def test_1_port_2_hosts_addresses():
    """One port for two hosts gives an address per host."""
    assert _smtpd(2525, '127.0.0.1, ::1').addresses == ['127.0.0.1:2525', '::1:2525']


# ---- 2 ports, 2 hosts -------------------------------------------------------


def test_2_ports_2_hosts_ports():
    """ "2525, 3535" gives the ports ["2525", "3535"]."""
    assert _smtpd('2525, 3535', '127.0.0.1, ::1').ports == ['2525', '3535']


def test_2_ports_2_hosts_hosts():
    """ "127.0.0.1, ::1" gives both hosts without blanks."""
    assert _smtpd('2525, 3535', '127.0.0.1, ::1').hosts == ['127.0.0.1', '::1']


def test_2_ports_2_hosts_addresses():
    """Two ports for two hosts pair them in order."""
    assert _smtpd('2525, 3535', '127.0.0.1, ::1').addresses == ['127.0.0.1:2525', '::1:3535']


# ---- 3 ports (a range), 2 hosts --------------------------------------------


def test_3_ports_2_hosts_ports():
    """ "2525, 2525:3535" keeps the second as one entry."""
    assert _smtpd('2525, 2525:3535', '127.0.0.1, ::1').ports == ['2525', '2525:3535']


def test_3_ports_2_hosts_hosts():
    """ "127.0.0.1, ::1" gives both hosts without blanks."""
    assert _smtpd('2525, 2525:3535', '127.0.0.1, ::1').hosts == ['127.0.0.1', '::1']


def test_3_ports_2_hosts_addresses():
    """ "2525:3535" binds the second host to both ports."""
    assert _smtpd('2525, 2525:3535', '127.0.0.1, ::1').addresses == ['127.0.0.1:2525', '::1:2525', '::1:3535']
