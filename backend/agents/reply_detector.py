"""Optional IMAP check that stops follow-ups after a prospect replies.

Unconfigured IMAP is a no-op so the manual status_update.py path still
works. A connection or login failure is reported and does not raise.
"""

import imaplib
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_REPLY_NOTE = "Reply detected via IMAP"


@dataclass(frozen=True)
class ImapSettings:
    host: str
    port: int
    user: str
    password: str
    use_ssl: bool
    folder: str


def _raw(name: str) -> str:
    return os.getenv(name, "").strip()


def imap_settings() -> ImapSettings | None:
    host = _raw("IMAP_HOST")
    user = _raw("IMAP_USER")
    password = os.getenv("IMAP_PASSWORD")
    password = "" if password is None else password.strip()
    if not host or not user or not password:
        return None
    port_raw = _raw("IMAP_PORT")
    port = int(port_raw) if port_raw else 993
    use_ssl = _raw("IMAP_SSL").lower() not in {"0", "false", "no", "off"} if _raw("IMAP_SSL") else True
    folder = _raw("IMAP_FOLDER") or "INBOX"
    return ImapSettings(host, port, user, password, use_ssl, folder)


def connect(settings: ImapSettings):
    if settings.use_ssl:
        client = imaplib.IMAP4_SSL(settings.host, settings.port)
    else:
        client = imaplib.IMAP4(settings.host, settings.port)
        client.starttls()
    client.login(settings.user, settings.password)
    return client


def _has_mail(client, email: str) -> bool:
    typ, data = client.search(None, "FROM", f'"{email}"')
    if typ != "OK" or not data or data[0] is None:
        return False
    raw = data[0]
    if isinstance(raw, bytes):
        raw = raw.strip()
    else:
        raw = str(raw).strip()
    return bool(raw)


def detect_replies(conn: sqlite3.Connection, client_factory=None) -> dict:
    """Mark contacted leads as replied when their address appears in IMAP.

    Returns a result dict. `configured` is false when IMAP is unset.
    `error` is set when a configured mailbox could not be read. Neither
    case raises.
    """
    settings = imap_settings()
    if settings is None:
        log.info(
            "IMAP is not configured. Reply detection skipped. "
            "Mark replies with status_update.py."
        )
        return {
            "configured": False,
            "checked": 0,
            "replied": 0,
            "note": "IMAP not configured. Use status_update.py to mark replies.",
        }

    factory = client_factory or connect
    try:
        client = factory(settings)
    except Exception as exc:
        log.warning("IMAP connection failed: %s", exc)
        return {"configured": True, "checked": 0, "replied": 0, "error": str(exc)}

    try:
        return _scan(conn, client, settings.folder)
    except Exception as exc:
        log.warning("IMAP scan failed: %s", exc)
        return {"configured": True, "checked": 0, "replied": 0, "error": str(exc)}
    finally:
        try:
            client.logout()
        except Exception:
            log.debug("IMAP logout failed", exc_info=True)


def _scan(conn: sqlite3.Connection, client, folder: str) -> dict:
    conn.row_factory = sqlite3.Row
    client.select(folder, readonly=True)
    rows = conn.execute(
        """
        SELECT id, email FROM leads
        WHERE email IS NOT NULL AND trim(email) != ''
          AND status IN ('contacted', 'ghosted')
        """
    ).fetchall()
    checked = 0
    replied = 0
    addresses: list[str] = []
    dry_run = os.getenv("DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for row in rows:
        email = str(row["email"]).strip()
        checked += 1
        if not _has_mail(client, email):
            continue
        addresses.append(email)
        replied += 1
        if dry_run:
            log.info("DRY RUN would mark %s replied.", email)
            continue
        conn.execute(
            """
            UPDATE leads
            SET status = 'replied',
                replied_at = ?,
                notes = CASE
                    WHEN notes IS NULL OR notes = '' THEN ?
                    ELSE notes
                END
            WHERE id = ?
            """,
            (now, _REPLY_NOTE, row["id"]),
        )
        log.info("Reply detected from %s. Follow-ups stopped.", email)
    if not dry_run:
        conn.commit()
    return {
        "configured": True,
        "checked": checked,
        "replied": replied,
        "addresses": addresses,
        "dry_run": dry_run,
    }
