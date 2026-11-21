"""Canada's anti-spam law gate for outbound email.

Consumer leads (homeowners, cottagers, landlords) and intent posts are
never cold-emailed. A quote-form submission is consent to reply, including
when the sender is a consumer. Direct mail, phone, and manual channels are
not email.
"""


def _field(lead, key: str) -> str:
    try:
        value = lead[key]
    except (KeyError, IndexError, TypeError):
        return ""
    if value is None:
        return ""
    return str(value).strip().casefold()


def cold_email_block_reason(lead) -> str | None:
    """Return why this lead must not be emailed, or None when a reply is allowed.

    `inbound_quote` is the only consent this pipeline records. It is set by
    the quote ingest path and nowhere else.
    """
    if _field(lead, "consent") == "inbound_quote":
        return None
    lead_type = _field(lead, "lead_type") or "business"
    channel = _field(lead, "contact_channel") or "email"
    if lead_type == "consumer":
        return "consumer"
    if lead_type == "intent":
        return "intent"
    if channel in {"manual", "direct_mail", "phone"}:
        return "channel"
    if lead_type != "business":
        return "lead_type"
    return None
