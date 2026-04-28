# Closer Agent

## Role
Generate a detailed call brief for every lead who has booked a meeting, so the human closer is fully prepared.

## Model
`claude-opus-4-5`

## Trigger
Runs as Stage 4 of the Director pipeline, or standalone via `python closer_agent.py`.

## Input
All leads with `status = 'meeting_booked'` and `call_brief_path IS NULL`.

> **Note:** Leads must be promoted to `meeting_booked` manually using `backend/status_update.py`
> until automated inbox polling is implemented.

## Output
Generates a Markdown call brief saved to `data/call_briefs/brief_{id}_{name}.md`.

The brief includes:
- 🎯 Lead at a Glance (3-4 bullet summary)
- 🔓 Opening Hook (a specific, strong opening line)
- 🔍 Discovery Questions (5-7 targeted questions)
- 🛡 Objection Handling (4 likely objections with rebuttals)
- 💰 Closing Framework (3-step close)
- ✅ Recommended Next Step

Updates the following columns in `leads`:
- `call_brief_path` — absolute path to the generated Markdown file
- `call_brief_at` — timestamp

## Human Role
The user is expected to join closing calls and use the brief as a guide.  
The agent does not make outbound calls.

## Config
| Env var             | Default | Purpose                        |
|---------------------|---------|--------------------------------|
| `ANTHROPIC_API_KEY` | —       | Required                       |
| `SENDER_NAME`       | `Chris` | Referenced in the brief        |
| `CLOSER_DELAY`      | `2.0`   | Seconds between API calls      |
| `DRY_RUN`           | `false` | Skip file write + DB update    |
