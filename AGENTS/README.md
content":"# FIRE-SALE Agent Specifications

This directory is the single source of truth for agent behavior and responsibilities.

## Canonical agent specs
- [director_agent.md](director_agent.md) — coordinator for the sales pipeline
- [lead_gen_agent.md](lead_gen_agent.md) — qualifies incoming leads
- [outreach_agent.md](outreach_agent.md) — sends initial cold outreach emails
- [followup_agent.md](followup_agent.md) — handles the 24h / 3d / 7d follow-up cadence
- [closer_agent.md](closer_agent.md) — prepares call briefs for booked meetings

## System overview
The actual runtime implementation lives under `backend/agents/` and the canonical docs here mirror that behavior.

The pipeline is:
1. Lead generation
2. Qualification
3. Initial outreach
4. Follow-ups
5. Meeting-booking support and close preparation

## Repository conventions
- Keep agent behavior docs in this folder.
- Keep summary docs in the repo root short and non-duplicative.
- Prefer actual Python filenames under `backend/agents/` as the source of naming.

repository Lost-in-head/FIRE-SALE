# FIRE-SALE Agent Specifications

This directory is the single source of truth for agent behavior and responsibilities.

## Canonical agent specs
- [director_agent.md](director_agent.md) — coordinator for the sales pipeline
- [lead_gen_agent.md](lead_gen_agent.md) — qualifies incoming leads
- [outreach_agent.md](outreach_agent.md) — sends initial cold outreach emails
- [followup_agent.md](followup_agent.md) — handles the 24h / 3d / 7d follow-up cadence
- [closer_agent.md](closer_agent.md) — prepares call briefs for booked meetings

## System overview
The actual runtime implementation lives under `backend/agents/` and the canonical docs here mirror that behavior.

The pipeline is:
1. Lead generation
2. Qualification
3. Initial outreach
4. Follow-ups
5. Meeting-booking support and close preparation

## Repository conventions
- Keep agent behavior docs in this folder.
- Keep summary docs in the repo root short and non-duplicative.
- Prefer actual Python filenames under `backend/agents/` as the source of naming.
