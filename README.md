# FIRE-SALE

Outbound prospecting and follow-up for two services the owner sells directly: contracting and tree removal, and website design for independent local businesses.

The first runs stay in test mode. Mail is redirected to the owner's own address. Prospects are not emailed until `TEST_MODE` is explicitly turned off.

## System shape
- `backend/db.py` — database and schema migration
- `backend/agents/` — pipeline implementation
- `config/prospecting.toml` — campaigns, areas, offer text, and the chain denylist
- `AGENTS/` — agent specs

## Pipeline
Prospecting → qualification → outreach → reply check → follow-up → call brief

## Start here
- [First internal test run](docs/first-internal-test-run.md) — secrets, local test mode, and what a run should do
- [AGENTS/README.md](AGENTS/README.md)

## Canonical docs
- [AGENTS/director_agent.md](AGENTS/director_agent.md)
- [AGENTS/prospector_agent.md](AGENTS/prospector_agent.md)
- [AGENTS/lead_gen_agent.md](AGENTS/lead_gen_agent.md)
- [AGENTS/outreach_agent.md](AGENTS/outreach_agent.md)
- [AGENTS/followup_agent.md](AGENTS/followup_agent.md)
- [AGENTS/closer_agent.md](AGENTS/closer_agent.md)
