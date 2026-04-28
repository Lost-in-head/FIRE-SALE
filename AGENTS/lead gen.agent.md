# Lead Generation Agent

## Role
Qualify unscored leads already in the database using the Anthropic Claude API.

## Model
`claude-opus-4-5`

## Trigger
Runs as Stage 1 of the Director pipeline, or standalone via `python lead_gen_agent.py`.

## Input
All leads in the `leads` table where `qualify_score IS NULL` and `status NOT IN ('dead', 'closed')`.

## Process
1. For each unscored lead, fetch visible text from `website_url` (if set) — up to 4 000 chars.
2. Send lead context to Claude with a structured prompt against the ICP.
3. Parse the returned JSON: `{ score, verdict, flags, action, summary }`.
4. Mark leads with score ≤ `SCORE_DEAD_THRESHOLD` (default 4) as `dead`; others as `qualified`.

## Output
Updates the following columns in `leads`:
- `qualify_score` (1–10)
- `qualify_verdict` (HOT / WARM / COLD)
- `qualify_flags`
- `qualify_action`
- `qualify_summary`
- `qualified_at`
- `status` → `qualified` or `dead`

## ICP (Ideal Customer Profile)
- Small web design agency or freelancer
- Needs more clients
- No strong existing outreach/sales system
- Active business, some online presence

## Config
| Env var                  | Default | Purpose                            |
|--------------------------|---------|------------------------------------|
| `ANTHROPIC_API_KEY`      | —       | Required                           |
| `SCORE_DEAD_THRESHOLD`   | `4`     | Leads at or below this → `dead`    |
| `LEAD_GEN_DELAY`         | `2.0`   | Seconds between API calls          |
| `DRY_RUN`                | `false` | Skip DB writes when `true`         |
