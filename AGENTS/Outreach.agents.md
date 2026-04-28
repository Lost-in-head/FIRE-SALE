# Outreach Agent

## Role
Generate and send personalised cold emails to qualified leads.

## Model
`claude-opus-4-5`

## Trigger
Runs as Stage 2 of the Director pipeline, or standalone via `python outreach_agent.py`.

## Input
All leads with `status = 'qualified'` and `last_outreach_at IS NULL`.

## Process
1. For each eligible lead, send business context to Claude.
2. Claude returns a JSON object: `{ subject, body }` — a personalised cold email.
3. Send the email via SMTP (Gmail by default).
4. Update lead status to `contacted`, store subject/body, set `last_outreach_at`.

## Output
Updates the following columns in `leads`:
- `status` → `contacted`
- `email_subject`
- `email_body`
- `last_outreach_at`
- `follow_up_count` → `0`

## Email Rules
- Maximum 5 sentences in body
- Conversational, not corporate
- References lead's business context
- Single CTA: book a short call (injects `BOOKING_LINK` if set)
- No buzzwords, no spam language

## Config
| Env var              | Default          | Purpose                        |
|----------------------|------------------|--------------------------------|
| `ANTHROPIC_API_KEY`  | —                | Required                       |
| `EMAIL_ADDRESS`      | —                | Sender Gmail address           |
| `EMAIL_APP_PASSWORD` | —                | Gmail app password             |
| `SMTP_HOST`          | `smtp.gmail.com` | SMTP server                    |
| `SMTP_PORT`          | `587`            | SMTP port (STARTTLS)           |
| `SENDER_NAME`        | `Chris`          | Display name in From header    |
| `BOOKING_LINK`       | (empty)          | Calendar link injected into CTA|
| `OUTREACH_DELAY`     | `3.0`            | Seconds between sends          |
| `DRY_RUN`            | `false`          | Skip send + DB write           |
