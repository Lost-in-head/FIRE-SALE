# Director Agent

## Role
Coordinate the full sales pipeline and stage execution.

## Responsible for
- running the pipeline in order
- checking pipeline health
- routing work to downstream agents
- logging a summary after each cycle

## Runtime
Implementation: `backend/agents/director_agent.py`

## Stages
Run in this order. Any stage can also be selected on its own with `--stage`.
1. `prospector` — gather new leads from the configured provider (OpenStreetMap by default)
2. `lead_gen` — qualify leads
3. `outreach` — initial contact
4. `followup` — scheduled follow-ups
5. `closer` — create call briefs

## Prospector inputs
The prospector reads `config/prospecting.toml`. CLI flags and environment variables override that file for one run: `--cities` / `PROSPECTOR_CITIES`, `--campaign` / `PROSPECTOR_CAMPAIGN`, `--queries` / `PROSPECTOR_QUERIES`, `--tags` / `PROSPECTOR_TAGS`, `--max-leads` / `PROSPECTOR_MAX_LEADS`, `--provider` / `PROSPECTOR_PROVIDER`, and `--dry-run` / `DRY_RUN`.

The default cities are Brampton and Mississauga in the Greater Toronto Area. Business types are campaigns in the config file, not code.

## Canonical source
Use this document and the implementation in `backend/agents/director_agent.py` as the authority for director behavior.
