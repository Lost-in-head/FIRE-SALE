# Outreach Agent

## Role
Send the first cold email to eligible leads.

## Runtime
Implementation: `backend/agents/outreach_agent.py`

## Inputs
- leads with `status = 'qualified'`
- `last_outreach_at IS NULL`
- a non-empty `email` (qualified leads without one are skipped and left unchanged)

## Output
Updates:
- `status` to `contacted`
- `email_subject`
- `email_body`
- `last_outreach_at`
- `follow_up_count` to `0`

## Email rules
- max 5 sentences in body
- conversational tone
- direct CTA to book a short call
- uses `BOOKING_LINK` if configured
- no spammy language

## Canonical source
Use this document together with `backend/agents/outreach_agent.py` as the source of truth for outbound outreach behavior.
