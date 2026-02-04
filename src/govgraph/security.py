"""Security utilities for GovGraph."""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# Blocked IP ranges for SSRF protection
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),       # Private
    ipaddress.ip_network("172.16.0.0/12"),    # Private
    ipaddress.ip_network("192.168.0.0/16"),   # Private
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local / Cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),        # Current network
    ipaddress.ip_network("224.0.0.0/4"),      # Multicast
    ipaddress.ip_network("240.0.0.0/4"),      # Reserved
    ipaddress.ip_network("100.64.0.0/10"),    # Carrier-grade NAT
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 private
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

# Blocked hostnames
BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "169.254.169.254",
}

SENSITIVE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "token",
    "key",
    "x-api-key",
}


def redact_url(url: str, *, sensitive_query_keys: set[str] | None = None, redaction: str = "REDACTED") -> str:
    """Redact sensitive query params (like API keys) from a URL string."""
    keys = sensitive_query_keys or SENSITIVE_QUERY_KEYS
    try:
        parts = urlsplit(url)
    except Exception:
        return url

    if not parts.query:
        return url

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted_pairs: list[tuple[str, str]] = []
    for k, v in query_pairs:
        if k.lower() in keys:
            redacted_pairs.append((k, redaction))
        else:
            redacted_pairs.append((k, v))

    redacted_query = urlencode(redacted_pairs, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, redacted_query, parts.fragment))


def is_safe_webhook_url(url: str) -> tuple[bool, str]:
    """
    Validate that a webhook URL is safe to call (not targeting internal resources).

    Returns (is_safe, error_message).
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Invalid URL format: {e}"

    # Must be HTTPS in production (allow HTTP for localhost testing)
    if parsed.scheme not in ("http", "https"):
        return False, f"URL scheme must be http or https, got: {parsed.scheme}"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL must have a hostname"

    # Check blocked hostnames
    hostname_lower = hostname.lower()
    if hostname_lower in BLOCKED_HOSTNAMES:
        return False, f"Hostname '{hostname}' is not allowed"

    # Resolve hostname to IP and check against blocked ranges
    try:
        # Get all IP addresses for the hostname
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        ips = set()
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ips.add(ip_str)

        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str)
                for blocked_range in BLOCKED_IP_RANGES:
                    if ip in blocked_range:
                        logger.warning(
                            "Blocked webhook URL targeting internal IP",
                            extra={"url": url, "resolved_ip": ip_str, "blocked_range": str(blocked_range)}
                        )
                        return False, f"URL resolves to blocked IP range: {blocked_range}"
            except ValueError:
                continue

    except socket.gaierror as e:
        # If we can't resolve, allow it (might be a valid external host)
        logger.debug(f"Could not resolve hostname {hostname}: {e}")

    return True, ""


def constant_time_compare(a: str, b: str) -> bool:
    """
    Compare two strings in constant time to prevent timing attacks.
    """
    if len(a) != len(b):
        return False

    result = 0
    for x, y in zip(a.encode(), b.encode()):
        result |= x ^ y
    return result == 0
