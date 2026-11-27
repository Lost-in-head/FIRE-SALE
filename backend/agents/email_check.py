"""Decide whether an address is safe to store on a lead.

A bad address is cleared. The lead stays. Nothing here sends mail.
MX lookup is optional and fail-soft: a DNS error keeps the address.
"""

import random
import re
import socket
import struct


_ADDRESS = re.compile(
    r"^[a-z0-9](?:[a-z0-9._%+\-]{0,62}[a-z0-9])?@"
    r"(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,}$"
)
_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "svg", "webp"}
_ROLE_LOCALS = {
    "noreply",
    "no-reply",
    "no.reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "do.not.reply",
    "do_not_reply",
    "mailer-daemon",
    "mailerdaemon",
    "postmaster",
}
_PLACEHOLDER_LOCALS = {"user", "name", "your", "yourname", "youremail", "test"}
_PLACEHOLDER_DOMAINS = {
    "domain.com",
    "domain.org",
    "domain.net",
    "email.com",
    "email.org",
    "example.com",
    "example.org",
    "example.net",
}
# Addresses that show up in page markup and are not a business inbox.
_JUNK_DOMAINS = {"schema.org", "godaddy.com", "squarespace.com"}


def assess_email(
    email: str | None,
    *,
    check_mx: bool = False,
    mx_lookup=None,
) -> tuple[str | None, str | None]:
    """Return (address, reason). Reason is set only when an address is cleared."""
    if email is None:
        return None, None
    cleaned = str(email).strip().lower().strip("<>").strip().strip(".")
    if not cleaned:
        return None, None
    if "email-protection" in cleaned or "cloudflare" in cleaned.split("@", 1)[-1]:
        return None, "tracking"
    if "@" not in cleaned or cleaned.count("@") != 1 or ".." in cleaned or any(
        char.isspace() for char in cleaned
    ):
        return None, "malformed"
    local, domain = cleaned.split("@", 1)
    if not local or not domain:
        return None, "malformed"
    if domain.rsplit(".", 1)[-1] in _IMAGE_EXT:
        return None, "image"
    if not _ADDRESS.fullmatch(cleaned):
        return None, "malformed"
    if _is_tracking(local, domain):
        return None, "tracking"
    if local.split("+", 1)[0] in _ROLE_LOCALS:
        return None, "role"
    if _is_placeholder(local, domain):
        return None, "placeholder"
    if check_mx and not _mx_ok(domain, mx_lookup):
        return None, "no_mx"
    return cleaned, None


def lookup_mx(domain: str, timeout: float = 2.0) -> bool | None:
    """True when mail can be delivered, False when it cannot, None on error.

    A domain with no MX record still counts when an A record exists, which
    is the usual fallback. Timeouts and resolver failures return None so
    the caller can keep the address.
    """
    try:
        return _lookup_mx(domain, timeout)
    except Exception:
        return None


def _is_tracking(local: str, domain: str) -> bool:
    if domain == "sentry.io" or domain.endswith(".sentry.io") or "sentry" in domain:
        return True
    if "wixpress" in domain:
        return True
    if domain == "wix.com" or domain.endswith(".wix.com"):
        if any(token in domain for token in ("sentry", "ingest", "error")):
            return True
        if re.fullmatch(r"[0-9a-f]{16,}", local):
            return True
    for base in _JUNK_DOMAINS:
        if domain == base or domain.endswith("." + base):
            return True
    return False


def _is_placeholder(local: str, domain: str) -> bool:
    if local in _PLACEHOLDER_LOCALS:
        return True
    if local == "email" and (
        domain in _PLACEHOLDER_DOMAINS or domain.startswith("domain.")
    ):
        return True
    for base in _PLACEHOLDER_DOMAINS:
        if domain == base or domain.endswith("." + base):
            return True
    return False


def _mx_ok(domain: str, mx_lookup) -> bool:
    lookup = mx_lookup or lookup_mx
    try:
        found = lookup(domain)
    except Exception:
        return True
    if found is None:
        return True
    return bool(found)


def _lookup_mx(domain: str, timeout: float) -> bool | None:
    saw_response = False
    for server in _resolvers():
        data = _query(domain, 15, server, timeout)
        if data is None:
            continue
        saw_response = True
        rcode = data[3] & 0x0F
        if rcode == 3:
            return False
        if rcode != 0:
            continue
        if _answer_count(data) > 0:
            return True
        address = _query(domain, 1, server, timeout)
        if address is None:
            return None
        address_rcode = address[3] & 0x0F
        if address_rcode == 3:
            return False
        if address_rcode == 0:
            return _answer_count(address) > 0
    if saw_response:
        return False
    return None


def _resolvers() -> list[str]:
    servers: list[str] = []
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    servers.append(parts[1])
    except OSError:
        return ["1.1.1.1", "8.8.8.8"]
    return servers or ["1.1.1.1", "8.8.8.8"]


def _query(domain: str, qtype: int, server: str, timeout: float) -> bytes | None:
    tid = random.randint(0, 65535)
    header = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    packet = header + _encode_name(domain) + struct.pack("!HH", qtype, 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, 53))
        data, _addr = sock.recvfrom(512)
    except OSError:
        return None
    finally:
        sock.close()
    if len(data) < 12 or struct.unpack("!H", data[:2])[0] != tid:
        return None
    return data


def _encode_name(name: str) -> bytes:
    encoded = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("ascii")
        if not raw or len(raw) > 63:
            raise ValueError("bad dns label")
        encoded += bytes([len(raw)]) + raw
    return encoded + b"\x00"


def _answer_count(data: bytes) -> int:
    return struct.unpack("!H", data[6:8])[0]
