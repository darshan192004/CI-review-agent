"""SSRF protection and command injection prevention utilities."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse


def validate_url_for_safety(url: str) -> bool:
    """Reject URLs targeting private/reserved IP ranges (SSRF prevention).
    Returns True if the URL is safe to fetch, False if it should be blocked."""

    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    # Block obvious SSRF targets
    blocked_hosts = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "[::1]",
        "metadata.google.internal",
        "169.254.169.254",
        "instance-data",
        "100.100.100.200",
    }
    if hostname.lower() in blocked_hosts:
        return False

    # Resolve hostname and check against private/reserved ranges
    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _, _, _, _, sockaddr in resolved:
            addr = ipaddress.ip_address(sockaddr[0])
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return False
    except (socket.gaierror, ValueError):
        # DNS resolution failed — reject for safety
        return False

    return True


def sanitize_command_arg(arg: str) -> str:
    """Remove shell metacharacters from command arguments.
    Use this when building subprocess command lists (NOT shell=True)."""

    # Remove null bytes
    cleaned = arg.replace("\x00", "")

    # Remove backticks
    cleaned = cleaned.replace("`", "")

    # Remove $() subshell syntax
    cleaned = re.sub(r"\$\([^)]*\)", "", cleaned)

    return cleaned


def is_safe_branch_name(branch: str) -> bool:
    """Validate branch names to prevent injection via branch parameters.
    Only allows alphanumeric, hyphens, underscores, slashes, and dots."""

    if not branch or len(branch) > 255:
        return False

    # Block paths that could traverse directories
    if ".." in branch:
        return False

    # Allow only safe characters
    return bool(re.match(r"^[a-zA-Z0-9._/\-]+$", branch))
