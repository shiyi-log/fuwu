"""Shared transport checks for credential-changing HTTP endpoints."""

from __future__ import annotations

import ipaddress
from typing import Mapping


def is_secure_transport(
    remote_address: str,
    headers: Mapping[str, str],
    *,
    tls: bool,
    trust_forwarded_proto: bool,
) -> bool:
    """Allow direct TLS/local initialization or an explicitly trusted proxy.

    A loopback peer can be a reverse proxy for an external HTTP caller. Proxy
    headers therefore disable the direct-loopback exception. The configured
    proxy must overwrite the protocol header and restrict direct backend access.
    Lists of protocols are rejected instead of guessing which hop to trust.
    """
    if any(headers.get(name) is not None for name in (
        "X-Forwarded-Proto", "X-Forwarded-For", "Forwarded",
    )):
        # BaseHTTPRequestHandler retains duplicate header lines, while WSGI
        # combines them. Do not let HTTPMessage.get() silently trust the first.
        get_all = getattr(headers, "get_all", None)
        if callable(get_all) and len(get_all("X-Forwarded-Proto", [])) != 1:
            return False
        return trust_forwarded_proto and headers.get("X-Forwarded-Proto", "").strip().casefold() == "https"
    if tls:
        return True
    try:
        return ipaddress.ip_address(remote_address).is_loopback
    except ValueError:
        return False
