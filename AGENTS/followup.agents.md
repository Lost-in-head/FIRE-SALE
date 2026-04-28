# Follow-Up Agent

## Role
Send time-sequenced follow-up emails to leads who haven't responded, and mark non-responders as `ghosted`.

## Model
`claude-opus-4-5`

## Trigger
Runs as Stage 3 of the Director pipeline, or standalone via `python followup_agent.py`.

## Input
All leads with `status = 'contacted'`, `last_outreach_at IS NOT NULL`, and `follow_up_count < 3`.

## Cadence
| Touch | Delay after last contact | Tone                                       |
|-------|--------------------------|---------------------------------------------|
| 1     | 24 hours                 | Soft, friendly nudge (2-3 sentences)        |
| 2     | 72 hours (3 days)        | Value hook specific to their business type  |
| 3     | 168 hours (7 days)       | Graceful final close, leaves door open      |

After Touch 3, lead status is updated to `ghosted`.

## Output
Updates the following columns in `leads`:
- `follow_up_count` (incremented)
- `last_outreach_at` (reset to now)
- `status` → `ghosted` after the 3rd touch

## Config
| Env var              | Default          | Purpose                             |
|----------------------|------------------|-------------------------------------|
| `ANTHROPIC_API_KEY`  | —                | Required                            |
| `EMAIL_ADDRESS`      | —                | Sender Gmail address                |
| `EMAIL_APP_PASSWORD` | —                | Gmail app password                  |
| `SMTP_HOST`          | `smtp.gmail.com` | SMTP server                         |
| `SMTP_PORT`          | `587`            | SMTP port (STARTTLS)                |
| `SENDER_NAME`        | `Chris`          | Display name in From header         |
| `BOOKING_LINK`       | (empty)          | Calendar link optionally injected   |
| `FOLLOWUP_DELAY`     | `3.0`            | Seconds between sends               |
| `DRY_RUN`            | `false`          | Skip send + DB write                |
