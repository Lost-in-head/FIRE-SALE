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
Run in this order. Any stage can also be selected on its own with `--stage`. A missing API key or a failed stage is logged and skipped, including when only one stage was requested. A prospector that inserts 0 leads, and any stage that skips, is also printed as a `::warning::` and added to the Actions job summary. The process still exits 0.
1. `prospector` — gather new leads from the configured provider (OpenStreetMap by default)
2. `lead_gen` — qualify leads against the campaign offer
3. `outreach` — initial contact (test mode redirects the recipient)
4. `replies` — optional IMAP reply detection
5. `followup` — scheduled follow-ups, skipped after a reply
6. `closer` — create call briefs

## Prospector inputs
The prospector reads `config/prospecting.toml`. CLI flags and environment variables override that file for one run: `--areas` / `PROSPECTOR_AREAS`, `--cities` / `PROSPECTOR_CITIES`, `--campaign` / `PROSPECTOR_CAMPAIGN`, `--queries` / `PROSPECTOR_QUERIES`, `--tags` / `PROSPECTOR_TAGS`, `--max-leads` / `PROSPECTOR_MAX_LEADS`, `--provider` / `PROSPECTOR_PROVIDER`, and `--dry-run` / `DRY_RUN`.

Default areas are `brampton` and `prince_edward_county`. Default campaign is `contracting_tree_removal`. `web_design` is the other shipped campaign. Mississauga is an optional city, not a default area.

## Mail
`TEST_MODE` defaults to on. Outreach and follow-up then go only to `TEST_RECIPIENT`. Set `TEST_MODE=false` to email the prospect. `DRY_RUN` sends nothing.

## Canonical source
Use this document and the implementation in `backend/agents/director_agent.py` as the authority for director behavior.
