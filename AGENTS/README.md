# FIRE-SALE Agent Specifications

This directory is the single source of truth for agent behavior and responsibilities.

## Canonical agent specs
- [prospector_agent.md](prospector_agent.md) — finds organizations and stores them as leads
- [director_agent.md](director_agent.md) — runs the pipeline in order
- [lead_gen_agent.md](lead_gen_agent.md) — scores leads against the campaign offer
- [outreach_agent.md](outreach_agent.md) — writes and sends the first email
- [followup_agent.md](followup_agent.md) — 24h / 3d / 7d follow-ups
- [closer_agent.md](closer_agent.md) — call briefs for booked meetings

## System overview
The runtime lives under `backend/agents/`. Campaigns, areas, and the words used in prompts live in `config/prospecting.toml`.

The pipeline is:
1. Prospector — search a named area for one campaign, including any enabled lead source
2. Qualification — score the lead for that campaign's offer
3. Initial outreach — test mode redirects this to the owner
4. Reply check — optional IMAP, otherwise `backend/status_update.py`
5. Follow-ups — skipped once a lead is `replied`
6. Meeting brief

## Repository conventions
- Keep agent behavior docs in this folder.
- Keep summary docs in the repo root short.
- Prefer the Python filenames under `backend/agents/` when naming things.
