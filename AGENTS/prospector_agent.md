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
The built-in provider is OpenStreetMap (Nominatim for cities without a pinned box, Overpass for the search). Other sources are listed in [docs/lead-sources.md](../docs/lead-sources.md) and toggled under `[sources]` in the config. Each one fails soft.

An area may set `bbox` as south, west, north, east. That rectangle is the search window. `config/areas/<key>.geojson` is the municipal polygon, and a point in the rectangle is kept only when it is inside the polygon. Georgetown and Norval proper are outside Brampton. Trenton is outside Prince Edward County. A one-off city such as Mississauga is geocoded and has no polygon.

No API key is required. Set `PROSPECTOR_CONTACT_EMAIL` so the User-Agent has a contact address.

## Campaigns
Exactly two campaigns ship:

- `contracting_tree_removal` — organizations that buy contracting and tree removal
- `web_design` — independent local businesses, preferring those with no website tag

`web_design` searches missing-website places first. A host on that campaign's `weak_website_hosts` list ranks next. Phone and address are stored either way.

Both campaigns share `[filters]`: a name denylist, plus any OSM `brand` or `brand:wikidata` tag when `drop_branded` is true. Competitor names (`exclude_keywords` on the campaign) are skipped too.

`[[filters.off_target]]` drops places that are not buyers: government offices, schools, places of worship, hospitals, chambers and boards of trade, charities, banks, gas stations, parking, and bus stops. Each campaign can add its own list under `[[campaigns.<name>.off_target]]`. A block has a `reason`, OSM `tags`, and name `keywords`. Edit those lists to change what is dropped. The run log counts drops by reason and does not include email addresses.

A bad email is cleared and the lead is kept. That covers placeholders (`user@domain.com`, `example.com`), tracking addresses (Sentry, Wix, Cloudflare email-protection), image filenames, and role accounts (`noreply`, `postmaster`). `check_mx = false` by default. Set it to true to clear an address when a lookup shows it cannot receive mail. A lookup error keeps the address.

Permanently closed OSM places (`disused:` / `was:` tags, or `opening_hours=closed`) are skipped. The same email or the same phone on a different business name is skipped. A point outside the area polygon is skipped.

Existing rows are not deleted. `python backend/agents/filter_existing.py` marks them `filtered` with `filter_reason` and clears a bad email. The director runs that step before scoring. It does not send email. `--dry-run` only reports the counts.

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
An address already on the OSM object is stored when it passes the email check. A placeholder, tracking, image, or role address is cleared and the lead is still inserted. Otherwise the agent may read that business's own site. Outreach skips a lead with no email.

## Dedupe
Skip when an existing lead matches, in order: `source` + `source_id`, website host, email, phone (digits only, so formatting differences still match), then the same OpenStreetMap place when a node and a way share a name and a location. A direct-mail address is also kept once. The same email or phone on two different business names is one lead.

A node and a way for one shop become one lead. The kept record uses the richer id and fills any blank phone, email, website, or address from the other element.

`max_leads` is split across the areas in the run. A transient Overpass response (429, 502, 503, 504) or a timeout is retried once.
