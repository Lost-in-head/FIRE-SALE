# Outreach Agent

## Role
Send the first cold email to eligible leads.

## Runtime
Implementation: `backend/agents/outreach_agent.py`

## Inputs
- leads with `status = 'qualified'`
- `last_outreach_at IS NULL`
- a non-empty `email` (qualified leads without one are skipped and left unchanged)
- not blocked by the contact rules below

## Output
Updates:
- `status` to `contacted`
- `email_subject`
- `email_body`
- `last_outreach_at`
- `follow_up_count` to `0`

## Who is not emailed
- `lead_type = consumer` (homeowner, cottager, landlord), unless `consent = inbound_quote` from the quote form
- `lead_type = intent` (a Kijiji, Reddit, or pasted Facebook post). These are for a manual reply
- `contact_channel` of `manual`, `direct_mail`, or `phone`

Follow-up uses the same rule. A blocked lead is left as it is.

## Email rules
- the body sells the offer on the lead's campaign (tree removal and contracting, or website design)
- max 5 sentences
- conversational tone
- direct CTA to book a short call
- uses `BOOKING_LINK` if configured
- test mode rewrites the SMTP recipient to `TEST_RECIPIENT` and names the intended prospect in the subject and body
- no message is sent when test mode is on and `TEST_RECIPIENT` is empty

## Canonical source
Use this document together with `backend/agents/outreach_agent.py` as the source of truth for outbound outreach behavior.
