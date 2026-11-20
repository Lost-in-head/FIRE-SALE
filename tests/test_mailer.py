"""Test mode redirects every message. SMTP honours host, port, and STARTTLS."""

import pytest

from agents import mailer


class _FakeSMTP:
    def __init__(self, host, port, timeout=30):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.sent = None
        _FakeSMTP.last = self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.user = user
        self.password = password

    def send_message(self, message):
        self.sent = message


def _env(monkeypatch, *, test_mode, recipient="owner@proton.test", starttls="true"):
    monkeypatch.setenv("TEST_MODE", test_mode)
    if recipient is None:
        monkeypatch.delenv("TEST_RECIPIENT", raising=False)
    else:
        monkeypatch.setenv("TEST_RECIPIENT", recipient)
    monkeypatch.setenv("EMAIL_ADDRESS", "sender@proton.test")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "bridge-secret")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1025")
    monkeypatch.setenv("SMTP_STARTTLS", starttls)
    monkeypatch.setenv("SENDER_NAME", "Chris")
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)


def test_blank_test_mode_is_on(monkeypatch):
    monkeypatch.delenv("TEST_MODE", raising=False)
    assert mailer.test_mode_enabled() is True
    monkeypatch.setenv("TEST_MODE", "false")
    assert mailer.test_mode_enabled() is False
    monkeypatch.setenv("TEST_MODE", "off")
    assert mailer.test_mode_enabled() is False


def test_test_mode_redirects_and_names_the_prospect(monkeypatch):
    _env(monkeypatch, test_mode="true", starttls="false")
    delivery = mailer.send_email(
        "prospect@shop.test",
        "Hello there",
        "We can help.",
        lead_name="Sam",
        business="Peel Golf Club",
    )
    sent = _FakeSMTP.last
    assert sent.host == "127.0.0.1"
    assert sent.port == 1025
    assert sent.started_tls is False
    assert sent.sent["To"] == "owner@proton.test"
    assert "prospect@shop.test" not in sent.sent["To"]
    assert "[TEST for prospect@shop.test]" in sent.sent["Subject"]
    assert "prospect@shop.test" in sent.sent.get_payload()
    assert "Sam" in sent.sent.get_payload()
    assert "Peel Golf Club" in sent.sent.get_payload()
    assert delivery.intended_to == "prospect@shop.test"
    assert sent.sent["X-Fire-Sale-Intended-To"] == "prospect@shop.test"


def test_starttls_is_on_by_default(monkeypatch):
    _env(monkeypatch, test_mode="false", starttls="")
    monkeypatch.delenv("SMTP_STARTTLS", raising=False)
    mailer.send_email("prospect@shop.test", "Hello", "Body")
    assert _FakeSMTP.last.sent["To"] == "prospect@shop.test"
    assert _FakeSMTP.last.started_tls is True
    assert "[TEST for" not in _FakeSMTP.last.sent["Subject"]


def test_test_mode_refuses_without_a_recipient(monkeypatch):
    _env(monkeypatch, test_mode="true", recipient=None)
    with pytest.raises(EnvironmentError, match="TEST_RECIPIENT"):
        mailer.send_email("prospect@shop.test", "Hello", "Body")


def test_live_mode_refuses_without_smtp_host(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(EnvironmentError, match="SMTP_HOST"):
        mailer.assert_outbound_allowed()


def test_dry_run_does_not_require_a_recipient(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.delenv("TEST_RECIPIENT", raising=False)
    mailer.assert_outbound_allowed()
