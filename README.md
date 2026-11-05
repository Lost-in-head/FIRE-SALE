# FIRE-SALE

This project is an AI sales system for outbound lead generation, qualification, outreach, follow-up, and meeting preparation.

## System shape
- `backend/db.py` — database and schema migration layer
- `backend/agents/` — implementation for the sales workflow
- `AGENTS/` — canonical agent specs

## Pipeline
Lead generation → qualification → initial outreach → follow-up → call briefing

## Canonical docs
- [AGENTS/README.md](AGENTS/README.md)
- [AGENTS/director_agent.md](AGENTS/director_agent.md)
- [AGENTS/lead_gen_agent.md](AGENTS/lead_gen_agent.md)
- [AGENTS/outreach_agent.md](AGENTS/outreach_agent.md)
- [AGENTS/followup_agent.md](AGENTS/followup_agent.md)
- [AGENTS/closer_agent.md](AGENTS/closer_agent.md)

The repo root docs are intentionally short and should not duplicate agent behavior details.
