# Prospector Agent

## Role
Find organizations that match the current campaign and store them as new leads. This stage does not qualify or contact anyone.

## Runtime
Implementation: `backend/agents/prospector_agent.py`

Targets file: `config/prospecting.toml`

```bash
python backend/agents/prospector_agent.py
python backend/agents/prospector_agent.py --dry-run
python backend/agents/prospector_agent.py --areas brampton --campaign web_design
python backend/agents/prospector_agent.py --areas prince_edward_county --campaign contracting_tree_removal
python backend/agents/prospector_agent.py --cities Mississauga --campaign web_design
python backend/agents/director_agent.py --stage prospector
```

## Source
The built-in provider is OpenStreetMap (Nominatim for cities without a pinned box, Overpass for the search).

An area may set `bbox` as south, west, north, east. Prince Edward County uses the county boundary from Nominatim, not the Picton town point. Brampton is pinned the same way. A one-off city such as Mississauga is geocoded.

No API key is required. Set `PROSPECTOR_CONTACT_EMAIL` so the User-Agent has a contact address.

## Campaigns
Exactly two campaigns ship:

- `contracting_tree_removal` — organizations that buy contracting and tree removal
- `web_design` — independent local businesses, preferring those with no website tag

`web_design` searches missing-website places first. A host on that campaign's `weak_website_hosts` list ranks next. Phone and address are stored either way.

Both campaigns share `[filters]`: a name denylist, plus any OSM `brand` or `brand:wikidata` tag when `drop_branded` is true. Competitor names (`exclude_keywords` on the campaign) are skipped too.

Each new lead stores `campaign`, `area`, and `business_type` when a target matched.

## Overrides

| Input | CLI | Environment |
| --- | --- | --- |
| Areas | `--areas` | `PROSPECTOR_AREAS` |
| One-off cities | `--cities` | `PROSPECTOR_CITIES` |
| Campaign | `--campaign` | `PROSPECTOR_CAMPAIGN` |
| Keywords | `--queries` | `PROSPECTOR_QUERIES` |
| OSM tags | `--tags` | `PROSPECTOR_TAGS` |
| Max new leads | `--max` or, on the director, `--max-leads` | `PROSPECTOR_MAX_LEADS` |
| Provider | `--provider` | `PROSPECTOR_PROVIDER` |
| Config path | `--config` | `PROSPECTOR_CONFIG` |
| Skip website reads | `--no-email-discovery` | `PROSPECTOR_FIND_EMAILS=false` |
| Preview only | `--dry-run` | `DRY_RUN=true` |

Pass areas or cities, not both. `--queries` replaces campaign keywords for that run.

## Email on the lead
An address already on the OSM object is stored. Otherwise the agent may read that business's own site. A lead with no email is still inserted. Outreach skips it later.

## Dedupe
Skip when an existing lead matches, in order: `source` + `source_id`, website host, then email.

`max_leads` is split across the areas in the run. A transient Overpass response (429, 502, 503, 504) or a timeout is retried once.
