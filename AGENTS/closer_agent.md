# Closer Agent

## Role
Prepare call briefs for leads who have booked meetings.

## Runtime
Implementation: `backend/agents/closer_agent.py`

## Inputs
- leads with `status = 'meeting_booked'`
- `call_brief_path IS NULL`

## Output
Creates a markdown brief and updates:
- `call_brief_path`
- `call_brief_at`

## Brief structure
- lead summary
- opening hook
- discovery questions
- objection handling
- close framework
- recommended next step

## Canonical source
Use this document together with `backend/agents/closer_agent.py` as the source of truth for the call-brief workflow.
