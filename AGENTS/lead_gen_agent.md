# Lead Generation Agent

## Role
Qualify unscored leads using business context and ICP matching.

## Runtime
Implementation: `backend/agents/lead_gen_agent.py`

## Inputs
- all leads with `qualify_score IS NULL`
- `status` not in `dead`, `closed`

## Output
Updates:
- `qualify_score`
- `qualify_verdict`
- `qualify_flags`
- `qualify_action`
- `qualify_summary`
- `qualified_at`
- `status` to `qualified` or `dead`

## ICP
- small web design agency or freelancer
- active business with an online presence
- needs more clients
- lacks a structured outbound sales system

## Canonical source
Use this document together with `backend/agents/lead_gen_agent.py` as the source of truth for lead qualification.
