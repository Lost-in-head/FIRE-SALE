# Follow-Up Agent

## Role
Send timed follow-up emails after the first outreach.

## Runtime
Implementation: `backend/agents/followup_agent.py`

## Inputs
- leads with `status = 'contacted'`
- `last_outreach_at IS NOT NULL`
- `follow_up_count < 3`

## Cadence
1. Touch 1 — 24 hours after first outreach
2. Touch 2 — 72 hours after last outreach
3. Touch 3 — 168 hours after last outreach

After the third touch, the lead can be marked `ghosted`.

## Output
- increments `follow_up_count`
- resets or updates `last_outreach_at`
- sets status to `ghosted` after final touch if no response

## Canonical source
Use this document together with `backend/agents/followup_agent.py` as the source of truth for follow-up timing and status transitions.
