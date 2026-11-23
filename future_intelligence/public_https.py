"""Direct, hostname-verified TLS for the public endpoints in the forecasting pilot."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import os
import re
import socket
import ssl
import sys
import time


class NonPublicAddress(ValueError):
    pass


class PublicHTTPS(http.client.HTTPSConnection):
    resolved_addresses: tuple[str, ...] = ()

    def connect(self):
        timeout = socket.getdefaulttimeout() if self.timeout is socket._GLOBAL_DEFAULT_TIMEOUT else self.timeout
        deadline = None if timeout is None else time.monotonic() + timeout
        records = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        self.resolved_addresses = tuple(sorted({record[4][0] for record in records}))
        if not records or not all(_public_unicast(value) for value in self.resolved_addresses):
            raise NonPublicAddress('nonpublic network address rejected')

        # Only TCP setup can try another OS-returned address. A TLS error is final.
        last_error = None
        for family, kind, proto, _, address in records:
            try:
                raw = socket.socket(family, kind, proto)
            except OSError as exc:
                last_error = exc
                continue
            with raw:
                if deadline is not None:
                    raw.settimeout(_remaining(deadline))
                try:
                    raw.connect(address)
                except OSError as exc:
                    last_error = exc
                    continue
                if ipaddress.ip_address(raw.getpeername()[0]) != ipaddress.ip_address(address[0]):
                    raise NonPublicAddress('peer differs from the resolved public address')
                if deadline is not None:
                    raw.settimeout(_remaining(deadline))
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
        raise last_error


def _public_unicast(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.is_site_local:
            return False
        ip = ip.ipv4_mapped or ip
    return ip.is_global and not ip.is_reserved and not ip.is_multicast


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('connection deadline exceeded')
    return remaining


PILOT_HOSTS = ('api.openai.com', 'api.bls.gov', 'www.bls.gov', 'freetsa.org')


def probe_egress(additional_tsa_host: str | None = None) -> dict:
    """Perform TLS handshakes only. No credentials, HTTP requests, or task claims."""
    hosts = PILOT_HOSTS
    if additional_tsa_host is not None:
        label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
        if len(additional_tsa_host) > 253 or not re.fullmatch(rf'{label}(?:\.{label})+', additional_tsa_host):
            raise ValueError('a DNS hostname is required')
        if additional_tsa_host not in hosts:
            hosts += (additional_tsa_host,)
    context = ssl.create_default_context()
    roots = sorted(hashlib.sha256(der).digest() for der in context.get_ca_certs(binary_form=True))
    results = []
    for host in hosts:
        connection = PublicHTTPS(host, timeout=8, context=context)
        item = {'host': host}
        try:
            connection.connect()
            tls = connection.sock
            item.update(status='VERIFIED_TLS', peer=tls.getpeername()[0], sni=tls.server_hostname,
                        tls_version=tls.version(), peer_cert_sha256=hashlib.sha256(tls.getpeercert(True)).hexdigest())
        except NonPublicAddress:
            item['status'] = 'ADDRESS_REJECTED'
        except (OSError, http.client.HTTPException) as exc:
            item.update(status='CONNECTION_FAILED', error=type(exc).__name__)
        finally:
            connection.close()
        item['addresses'] = list(connection.resolved_addresses)
        results.append(item)
    try:
        netns = os.readlink('/proc/self/ns/net')
    except OSError:
        netns = None
    return {'protocol': 'fi.worker-egress.v1', 'observed_at': datetime.now(timezone.utc).isoformat(), 'platform': sys.platform,
            'pid': os.getpid(), 'network_namespace': netns, 'resolver_module': socket.getaddrinfo.__module__,
            'sitecustomize_loaded': 'sitecustomize' in sys.modules,
            'loaded_ca_set_sha256': hashlib.sha256(b''.join(roots)).hexdigest(),
            'endpoints': results, 'tls_passed': all(row['status'] == 'VERIFIED_TLS' for row in results),
            'production_acceptance': False}
