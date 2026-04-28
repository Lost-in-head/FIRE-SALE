# Tools

## Phase 1 (Current)
- **Python 3.12** — agent runtime
- **SQLite** (`data/fire_sale_leads.sqlite3`) — lead database, managed by `backend/db.py`
- **Anthropic Claude API** (`claude-opus-4-5`) — lead scoring, email generation, call briefs
- **Gmail / SMTP** — outbound cold email delivery
- **Calendar booking link** (`BOOKING_LINK` env var) — meeting scheduling CTA in emails

## Phase 2
- CRM integration (HubSpot or lightweight alternative)
- IMAP inbox polling — auto-detect replies and promote lead status
- Scheduler (cron / GitHub Actions) — run Director on a regular cadence

## Phase 3
- Automated lead scraping (Clutch, Google Maps, etc.)
- Full AI integration across all touchpoints
