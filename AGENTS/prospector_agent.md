# Prospector Agent

## Role
Find businesses that match the current offer and store them as new leads. This stage does not qualify or contact anyone.

## Runtime
Implementation: `backend/agents/prospector_agent.py`

Provider interface: `backend/agents/providers/`

Targets file: `config/prospecting.toml`

Run it on its own:

```bash
python backend/agents/prospector_agent.py
python backend/agents/prospector_agent.py --dry-run
python backend/agents/prospector_agent.py --cities "Brampton,Mississauga" --campaign web_design
python backend/agents/director_agent.py --stage prospector
```

Or as the first stage of `backend/agents/director_agent.py`.

## Source
The built-in provider is OpenStreetMap.

1. Geocode each city with Nominatim.
2. Search that bounding box with the public Overpass API.
3. Keep the business name, website, phone, address, and OSM id (`node/123`, `way/456`, or `relation/789`).

Nominatim's usage policy is part of the provider: an identifying `User-Agent` (`PROSPECTOR_CONTACT_EMAIL` is included when set), about one request per second, and a disk cache at `data/nominatim_cache.json`.

No API key is required. Google Places is not implemented. A later provider implements `LeadProvider.search` and is added in `agents.providers.get_provider` without changing the director.

## Targets
Default region: Greater Toronto Area, Ontario, Canada.

Default cities: Brampton and Mississauga. Toronto, Vaughan, Markham, and Oakville are already listed under `region.places`, so adding one of them to `default_cities` (or passing `--cities`) is a config change. Any other Ontario city works too: a name that is not in `region.places` is geocoded as `"<city>, Ontario, Canada"`.

Business types are campaigns in `config/prospecting.toml`, not code. Each campaign maps an offer to:

- `keywords` — matched against the business name
- `tags` — exact OpenStreetMap `key=value` filters, any of which may match

The shipped file includes `web_design`, `home_trades`, and `food_and_drink` as editable examples. `default_campaign` chooses which one a normal run uses. Change it when the offer changes.

Per-run overrides:

| Input | CLI | Environment |
| --- | --- | --- |
| Cities | `--cities` | `PROSPECTOR_CITIES` |
| Campaign | `--campaign` | `PROSPECTOR_CAMPAIGN` |
| Keywords | `--queries` | `PROSPECTOR_QUERIES` |
| OSM tags | `--tags` | `PROSPECTOR_TAGS` |
| Max new leads | `--max` or, on the director, `--max-leads` | `PROSPECTOR_MAX_LEADS` |
| Provider | `--provider` | `PROSPECTOR_PROVIDER` |
| Config path | `--config` | `PROSPECTOR_CONFIG` |
| Skip website reads | `--no-email-discovery` | `PROSPECTOR_FIND_EMAILS=false` |
| Preview only | `--dry-run` | `DRY_RUN=true` |

`--queries` replaces the campaign keywords and leaves its tags in place when a campaign is selected. Passing keywords or tags without a campaign does not inherit the default campaign. A run with neither keywords nor tags is rejected.

## Email
An email already published on the OSM object (`email` or `contact:email`) is stored as-is.

Otherwise, when email discovery is on, the agent may read the business's own homepage and contact page. Requests time out, wait between pages, and skip paths disallowed by that site's `robots.txt`. No search engine or directory is scraped.

A business with no email is still inserted. Outreach skips qualified leads that have no email, so the rest of the pipeline keeps running.

## Dedupe and storage
A candidate is skipped when it matches an existing lead in this order:

1. `source` + `source_id`
2. website host, ignoring scheme, path, and a leading `www`
3. email, case-insensitive

`backend/db.py` adds `source`, `source_id`, `city`, `phone`, `address`, and `prospected_at` to existing databases. `email` stays unique and nullable, so several leads may have no email. Dry-run resolves and dedupes without writing leads.

`max_leads` is a cap for the whole run, split across the configured cities so the first city does not consume every slot. A transient Overpass response (429, 502, 503, 504) or a timeout is retried once and then recorded on the run result as an error. A 500 is not retried. Malformed elements are skipped.

## Known limitations
- OpenStreetMap coverage is uneven. Many small businesses have no website, phone, or email on the map, and some have no entry at all.
- Name and tag search misses businesses whose OSM tags do not match the campaign. Fix that in the config file, not in code.
- Public Nominatim and Overpass instances rate-limit and occasionally time out. One failed city is logged and skipped; it does not stop later pipeline stages.
- Set `PROSPECTOR_CONTACT_EMAIL` so the User-Agent identifies who to contact, which Nominatim asks applications to do.
- The daily workflow uses the config-file defaults unless repository variables override them. Keep `max_leads` modest so a scheduled run stays within the public APIs' limits.
