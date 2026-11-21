# Lead Generation Agent

## Role
Score unscored leads against the offer on their campaign.

## Runtime
Implementation: `backend/agents/lead_gen_agent.py`

The prompt is built from `config/prospecting.toml`: that campaign's `offer`, `sender_pitch`, `service_area`, and `scoring_notes`. A lead with no campaign does not inherit the other offer.

## Inputs
- leads with `qualify_score IS NULL`
- `status` not in `dead`, `closed`

## Output
Updates `qualify_score`, `qualify_verdict`, `qualify_flags`, `qualify_action`, `qualify_summary`, `qualified_at`, and `status` (`qualified` or `dead`).

Scores at or below `SCORE_DEAD_THRESHOLD` (default 4) are marked `dead`.

## Fit
- Contracting and tree removal: the organization has grounds, a site, or property someone has to maintain, and it is not another tree company or a chain.
- Website design: an independent local business, stronger when no website (or only a weak one) was found. A phone number and address are enough.
- The website-design campaign also records a mechanical website score: no site, no HTTPS, no viewport, a copyright year five or more years old, or a slow or broken page. That score is blended into the model score (60% model, 40% mechanical). 10 means no usable site.

## Canonical source
This document and `backend/agents/lead_gen_agent.py`.
