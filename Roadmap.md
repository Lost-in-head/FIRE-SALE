# Roadmap

## Phase 1 — Foundation
- Agents and the director are in place
- Two campaigns are configurable in `config/prospecting.toml`: contracting and tree removal, and website design
- Two areas are configurable: Brampton, and Prince Edward County (county boundary, not Picton alone)
- Mississauga stays available as an optional city

## Phase 2 — Safe internal runs (CURRENT)
- Test mode redirects every outbound email to the owner
- SMTP host, port, and STARTTLS are configurable (Proton Mail Bridge or Proton SMTP)
- Optional IMAP reply detection stops follow-ups; manual status updates still work
- The daily Actions run restores the latest lead database and warns when a stage inserts nothing or skips for a missing secret
- Real prospects are emailed only when `TEST_MODE` is explicitly off and the mailbox secrets are set

## Phase 3 — After the owner has run it on themselves
- Turn test mode off for one campaign and one area
- Watch replies and mark meetings with `status_update.py` (or IMAP, if it is configured)
- Add a booking link once the calendar is chosen

## Phase 4 — Optimization
- Tighten which targets inside a campaign are worth keeping
- Compare the two offers
- Track which emails get a reply

## Owner
- Run the first internal test in test mode before any live send
- Confirm the offer text and the service-area sentence in `config/prospecting.toml`
- Add the secrets listed in [docs/first-internal-test-run.md](docs/first-internal-test-run.md)

## Pipeline
- Find leads from OpenStreetMap
- Score and write copy for the campaign on the lead
- Send only to the owner until test mode is turned off
- Stop follow-ups when someone replies
