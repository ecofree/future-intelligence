import hashlib
import socket
import ssl
import unittest
from unittest.mock import MagicMock, patch

from future_intelligence.public_https import NonPublicAddress, PILOT_HOSTS, PublicHTTPS, probe_egress


def record(ip):
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (ip, 443))


class PublicHTTPSTests(unittest.TestCase):
    def test_entire_dns_answer_must_be_public_unicast(self):
        for records in ([], [record('8.8.8.8'), record('198.18.0.1')], [record('224.0.0.1')]):
            with self.subTest(records=records), patch('socket.getaddrinfo', return_value=records), \
                    patch('socket.socket') as create:
                with self.assertRaises(NonPublicAddress):
                    PublicHTTPS('api.openai.com').connect()
                create.assert_not_called()

    def test_uses_os_sockaddr_and_original_sni_after_tcp_failure(self):
        host = 'api.openai.com'
        first, second = record('8.8.8.8'), record('1.1.1.1')
        a, b = MagicMock(), MagicMock()
        a.connect.side_effect = OSError()
        b.getpeername.return_value = second[4]
        conn = PublicHTTPS(host)
        with patch('socket.getaddrinfo', return_value=[first, second]) as resolve, \
                patch('socket.socket', side_effect=[a, b]), patch.object(conn._context, 'wrap_socket') as wrap:
            conn.connect()
        resolve.assert_called_once_with(host, 443, type=socket.SOCK_STREAM)
        a.connect.assert_called_once_with(first[4])
        b.connect.assert_called_once_with(second[4])
        wrap.assert_called_once_with(b, server_hostname=host)
        self.assertIs(conn.sock, wrap.return_value)

    def test_unexpected_peer_or_tls_error_stops_before_a_second_connection(self):
        dns = [record('8.8.8.8'), record('1.1.1.1')]
        for peer, error in [('9.9.9.9', NonPublicAddress), ('8.8.8.8', ssl.SSLError)]:
            conn = PublicHTTPS('api.bls.gov', timeout=2)
            with self.subTest(peer=peer), patch('socket.getaddrinfo', return_value=dns), \
                    patch('socket.socket') as create, patch.object(conn._context, 'wrap_socket') as wrap:
                create.return_value.getpeername.return_value = (peer, 443)
                wrap.side_effect = ssl.SSLError()
                with self.assertRaises(error):
                    conn.connect()
                create.assert_called_once()
                self.assertIsNone(conn.sock)

    def test_probe_reports_handshakes_and_keeps_a_failed_host_visible(self):
        with patch('future_intelligence.public_https.PublicHTTPS') as factory:
            connections = [MagicMock() for _ in PILOT_HOSTS]
            factory.side_effect = connections
            for host, conn in zip(PILOT_HOSTS, connections):
                conn.resolved_addresses = ('8.8.8.8',)
                conn.sock.getpeername.return_value = ('8.8.8.8', 443)
                conn.sock.server_hostname = host
                conn.sock.version.return_value = 'TLSv1.3'
                conn.sock.getpeercert.return_value = b'synthetic-certificate'
            connections[-1].connect.side_effect = ssl.SSLCertVerificationError()
            report = probe_egress()
        self.assertFalse(report['tls_passed'])
        self.assertFalse(report['production_acceptance'])
        self.assertEqual([e['host'] for e in report['endpoints']], list(PILOT_HOSTS))
        self.assertEqual(report['endpoints'][0]['peer_cert_sha256'], hashlib.sha256(b'synthetic-certificate').hexdigest())
        self.assertEqual(report['endpoints'][-1]['error'], 'SSLCertVerificationError')
        for conn in connections:
            conn.request.assert_not_called()
            conn.close.assert_called_once()

    def test_additional_tsa_is_a_hostname_only(self):
        for value in ('https://example.org/tsa', 'example.org:8443', 'host.', 'user@example.org'):
            with self.subTest(value=value), patch('socket.getaddrinfo') as dns:
                with self.assertRaises(ValueError):
                    probe_egress(value)
                dns.assert_not_called()
