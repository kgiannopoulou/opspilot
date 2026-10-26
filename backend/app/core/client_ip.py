"""The real client address, for rate limits, login throttling and the audit trail.

Behind the Next.js proxy every request arrives from the proxy's address, so per-IP limits would lump all users
together. `X-Forwarded-For` fixes that — but it is a request header anyone can send. It is only believed when the
TCP peer is a configured trusted proxy (OPSPILOT_TRUSTED_PROXIES), and then the right-most address that is not itself
a trusted proxy is taken: entries to its left were written by the client and may be forged.
"""

import ipaddress
from functools import lru_cache

from fastapi import Request

from app.core.config import get_settings

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache
def _trusted(networks: tuple[str, ...]) -> tuple[Network, ...]:
    return tuple(ipaddress.ip_network(n, strict=False) for n in networks)


def _parse(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip()) if value else None
    except ValueError:
        return None


def resolve_client_ip(peer: str | None, forwarded_for: str | None, trusted: tuple[str, ...]) -> str | None:
    networks = _trusted(trusted)
    address = _parse(peer)
    if address is None:
        return None  # test clients and unix sockets report hostnames

    def is_trusted(a: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(a in n for n in networks)

    if not is_trusted(address) or not forwarded_for:
        return str(address)
    for hop in reversed(forwarded_for.split(",")):
        candidate = _parse(hop)
        if candidate is None:
            break  # malformed chain: stop believing it
        if not is_trusted(candidate):
            return str(candidate)
        address = candidate
    return str(address)


def client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    return resolve_client_ip(peer, request.headers.get("x-forwarded-for"), tuple(get_settings().trusted_proxies))
