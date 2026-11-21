# Lead sources

OpenStreetMap is still the default business search. The sources below are optional, configured in `config/prospecting.toml` under `[sources.*]`, and each one fails soft. Set `enabled = false` to skip one. A source that cannot be read does not stop the others.

Every stored row keeps `source`, `campaign`, `area`, and `contact_channel`. Dedupe is source id, then website host, then email, then the same OpenStreetMap place, then a direct-mail address.

Nothing in this pipeline posts to Kijiji, Reddit, Facebook, a chamber, or a directory. Intent rows are for a person to answer by hand.

## Need-help posts

`[sources.need_help]` reads public Reddit RSS and the historical Kijiji wanted RSS. There is no login. Requests wait `min_interval_seconds`.

A matching post is an `intent` lead with `contact_channel = manual`. Outreach will not email it.

Kijiji removed public RSS in 2024. The configured URLs are the old wanted-ad feeds. When the host returns 403, that feed is skipped and the run continues with Reddit.

Subreddits: `r/Brampton`, `r/PrinceEdwardCounty`, and `r/BellevilleOntario` (Belleville, Quinte, and the county).

## Storm alerts

`[sources.storm_alerts]` reads Environment Canada ATOM feeds:

- Brampton: `https://weather.gc.ca/rss/battleboard/onrm27_e.xml` (Mississauga - Brampton)
- Prince Edward County: `https://weather.gc.ca/rss/battleboard/onrm30_e.xml` (Picton - Sandbanks Park)

Wind, ice, and thunderstorm warnings are stored in the `alerts` table and listed in the Actions job summary. Fog and "no alerts" are ignored.

`notify = false` by default. Set it to `true` to email a heads-up. Test mode still redirects that message to `TEST_RECIPIENT`. No prospect is emailed.

## Permits

Brampton publishes building permits on its ArcGIS layer, including demolition and new dwellings. Recent issued demolition, dwelling, and addition permits become `consumer` leads with `contact_channel = direct_mail`. They are not emailed.

Prince Edward County's open data portal (parcels, zoning, boundaries) does not publish individual building, demolition, or tree permits. Applications are in Cloudpermit. The monthly construction reports are totals, not addresses. That area is skipped on purpose.

`python backend/agents/direct_mail_export.py --out data/direct_mail.csv` writes the address list. It does not include email addresses. There is no free public recently-sold feed. ONLAND is paid, and listing sites are not read.

## Quote intake

Static pages live in `docs/quote/` and can be published with GitHub Pages from the `/docs` folder:

- `docs/quote/contracting-tree-removal/`
- `docs/quote/web-design/`

The form downloads `quote.json` and can open a GitHub issue from `.github/ISSUE_TEMPLATE/quote.yml`. It does not post to a server. The owner imports a file:

```bash
python backend/agents/quote_ingest.py --json quote.json
python backend/agents/quote_ingest.py --issue issue-body.md
```

Drop JSON files in `data/quote_inbox/` and the prospector will import the ones that match the run. A quote is `consent = inbound_quote`, which allows a reply. That includes a homeowner who used the form. It is not consent to cold-email anyone else.

The pages name the repository in `data-repo`. Change that if the GitHub repo moves. No API key is required.

## Directories

`[sources.directories]` fetches a page only after `robots.txt` allows it. Yellow Pages search URLs are requested when the robots file allows `/search/si/`. A host that refuses `robots.txt` is skipped.

The Brampton Board of Trade directory (`https://members.bramptonbot.com/member-directory`) is behind a bot check. The Prince Edward County Chamber directory (`https://www.pecchamber.com/Member-Directory`) is public, but the member records are loaded by a script and are not in the HTML. Both are skipped until a public listing is actually in the page. A page that does include JSON-LD or a `member-card` block is parsed, and a matching business can fill a blank phone, email, or website on a lead already stored.

## Website quality

For `web_design`, lead gen scores the page: no site, no HTTPS, no viewport, a copyright year five or more years old, or a slow or broken response. The mechanical score is 0 (finished site) to 10 (no site). It is blended into the model score, 60% model and 40% mechanical, only for the campaign that prefers a missing website.

## Facebook

`[[facebook_groups]]` is a reading list. The program does not fetch Facebook.

```bash
python backend/agents/facebook_manual.py \
  --area brampton \
  --campaign web_design \
  --url "https://www.facebook.com/groups/example/posts/1" \
  --text "Looking for a website" \
  --name "Pat"
```

That stores a manual intent lead. It does not comment or message.

Groups with public URLs:

- Peel Village and Eldomar Heights, Brampton — `https://www.facebook.com/groups/267193853354489`
- Brampton, Mississauga, Toronto GTA Buy Sell — `https://www.facebook.com/groups/2273051266267045`
- Your Prince Edward County Community Page — `https://www.facebook.com/groups/PrinceEdwardCountyCommunityPage`
- Belleville Neighbours — `https://www.facebook.com/groups/1029364787229190`

## Homeowners, cottagers, and landlords

`lead_type = consumer` is for a person, not a business. Canada's anti-spam law means they are never cold-emailed. Permit rows use direct mail. Outreach and follow-up both refuse a consumer, an intent post, and a manual, phone, or direct-mail channel. The exception is `consent = inbound_quote` from the quote form.

## Areas

The bbox is only the search rectangle. `config/areas/brampton.geojson` and `config/areas/prince_edward_county.geojson` are the municipal polygons. Georgetown and Norval proper fall outside Brampton. Trenton falls outside Prince Edward County. A point is kept only when it is inside the polygon.

## Chains

The denylist includes Sutton (the real-estate franchise) and Country Inn & Suites, plus the same class of hotel franchise already used for Hilton, Marriott, and the others.
