"""Outbound email with a safe test-mode redirect and configurable SMTP.

TEST_MODE defaults to on. While it is on, every message is sent to
TEST_RECIPIENT and the prospect address is only printed in the subject
and body. Real recipients are used only when TEST_MODE is explicitly off.
DRY_RUN never reaches this module's send function.
"""

import os
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText


def _raw(name: str) -> str:
    return os.getenv(name, "").strip()


def _enabled(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def dry_run_enabled() -> bool:
    return _enabled("DRY_RUN", default=False)


def test_mode_enabled() -> bool:
    """Blank or unset TEST_MODE stays on so a first run cannot email prospects."""
    return _enabled("TEST_MODE", default=True)


def test_recipient() -> str:
    return _raw("TEST_RECIPIENT")


def describe_outbound_policy() -> str:
    if dry_run_enabled():
        return "Outbound policy: DRY_RUN. No email will be sent."
    if test_mode_enabled():
        recipient = test_recipient() or "(missing TEST_RECIPIENT — sends will be refused)"
        return (
            "Outbound policy: TEST_MODE. Every message is redirected to "
            f"{recipient}. Prospects are not emailed."
        )
    return (
        "Outbound policy: LIVE. TEST_MODE is explicitly off, so messages "
        "go to the prospect."
    )


def assert_outbound_allowed() -> None:
    """Raise before a stage sends, so a bad setup cannot leak a real email."""
    if dry_run_enabled():
        return
    if test_mode_enabled():
        if not test_recipient():
            raise EnvironmentError(
                "TEST_MODE is on but TEST_RECIPIENT is empty. "
                "Refusing to email prospects."
            )
        return
    missing = [
        name
        for name in ("EMAIL_ADDRESS", "EMAIL_APP_PASSWORD", "SMTP_HOST")
        if not _raw(name)
    ]
    if missing:
        raise EnvironmentError(
            "Cannot send email. Missing " + ", ".join(missing) + "."
        )


@dataclass(frozen=True)
class Delivery:
    to: str
    subject: str
    body: str
    intended_to: str


def prepare_delivery(
    to_email: str,
    subject: str,
    body: str,
    *,
    lead_name: str = "",
    business: str = "",
) -> Delivery:
    """Return the message that should actually be handed to SMTP."""
    intended = to_email.strip()
    if not test_mode_enabled():
        return Delivery(to=intended, subject=subject, body=body, intended_to=intended)
    recipient = test_recipient()
    if not recipient:
        raise EnvironmentError(
            "TEST_MODE is on but TEST_RECIPIENT is empty. "
            "Refusing to email prospects."
        )
    who = lead_name.strip() or "the contact"
    place = business.strip() or "their business"
    banner = (
        "TEST MODE - this message was NOT sent to the prospect.\n"
        f"Intended recipient: {who} <{intended}> at {place}\n"
        "----\n\n"
    )
    return Delivery(
        to=recipient,
        subject=f"[TEST for {intended}] {subject}",
        body=banner + body,
        intended_to=intended,
    )


def _port(name: str, default: int) -> int:
    raw = _raw(name)
    if not raw:
        return default
    return int(raw)


def send_email(
    to_email: str,
    subject: str,
    body: str,
    *,
    lead_name: str = "",
    business: str = "",
) -> Delivery:
    """Send one message. Test mode rewrites the recipient first."""
    prepared = prepare_delivery(
        to_email,
        subject,
        body,
        lead_name=lead_name,
        business=business,
    )
    host = _raw("SMTP_HOST")
    from_email = _raw("EMAIL_ADDRESS")
    password = _raw("EMAIL_APP_PASSWORD")
    from_name = _raw("SENDER_NAME") or "Chris"
    if not host or not from_email or not password:
        missing = [
            name
            for name, value in (
                ("SMTP_HOST", host),
                ("EMAIL_ADDRESS", from_email),
                ("EMAIL_APP_PASSWORD", password),
            )
            if not value
        ]
        raise EnvironmentError(
            "Cannot send email. Missing " + ", ".join(missing) + "."
        )
    message = MIMEText(prepared.body, "plain")
    message["Subject"] = prepared.subject
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = prepared.to
    if prepared.intended_to != prepared.to:
        message["X-Fire-Sale-Intended-To"] = prepared.intended_to

    with smtplib.SMTP(host, _port("SMTP_PORT", 587), timeout=30) as server:
        server.ehlo()
        if _enabled("SMTP_STARTTLS", default=True):
            server.starttls()
            server.ehlo()
        server.login(from_email, password)
        server.send_message(message)
    return prepared
