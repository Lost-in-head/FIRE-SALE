# First internal test run

Run FIRE-SALE on yourself before it emails anyone else. Test mode is the default. With `TEST_MODE` left blank or set to `true`, every outbound message is delivered to `TEST_RECIPIENT`. The prospect's address is printed in the subject and the body and is not the SMTP recipient.

`DRY_RUN=true` (or `--dry-run`) goes further: no email is sent and lead rows are not written.

## GitHub Actions secrets and variables

Add these on the repository before the first scheduled or manual run. Secrets hold credentials. Variables hold non-secret settings.

### Secrets

| Name | Required for the first test | Value |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Yes, if scoring and copy should run on the default provider | Anthropic key. Used when `LLM_API_KEY` is empty and the provider is Anthropic. If neither key is set, those stages skip and the job stays green, with a warning. |
| `LLM_API_KEY` | Only when `LLM_PROVIDER` is not Anthropic | Key for the selected host. When set, it wins over `ANTHROPIC_API_KEY`. |
| `TEST_RECIPIENT` | Yes | Your own mailbox. This is the only address that receives mail while test mode is on. |
| `EMAIL_ADDRESS` | Yes, to actually deliver the test copy | The Proton address the message is sent from. |
| `EMAIL_APP_PASSWORD` | Yes, to actually deliver the test copy | Proton Mail Bridge password, or a Proton SMTP submission token. |
| `SENDER_NAME` | Optional | Name in the From header. Defaults to Chris. |
| `BOOKING_LINK` | Optional | Included in the prompt when set. |
| `IMAP_USER` | Optional | Mailbox login for reply detection. Leave empty to skip IMAP. |
| `IMAP_PASSWORD` | Optional | Mailbox password or Bridge password. |

### Variables

| Name | First-run value | Notes |
| --- | --- | --- |
| `TEST_MODE` | leave unset, or `true` | Set to `false` only when you mean to email prospects. The workflow forces test mode back on for any other value. |
| `SMTP_HOST` | `127.0.0.1` for Bridge on a machine you control, or `smtp.protonmail.ch` for a submission token | There is no Gmail default. |
| `SMTP_PORT` | `1025` for Bridge, `587` for Proton submission | Defaults to 587 when unset. |
| `SMTP_STARTTLS` | `true` | Set `false` only if the Bridge port does not use STARTTLS. |
| `IMAP_HOST` | empty, or `127.0.0.1` (Bridge) / `imap.protonmail.ch` | Empty skips reply detection. |
| `IMAP_PORT` | `1143` for Bridge, `993` for Proton IMAP | Defaults to 993. |
| `IMAP_SSL` | `false` for Bridge STARTTLS, `true` for port 993 | |
| `PROSPECTOR_CONTACT_EMAIL` | your address | Put on the Nominatim User-Agent. |
| `OVERPASS_URL` | leave unset | Defaults to `https://overpass-api.de/api/interpreter`. Set it only when that host is refusing connections. |
| `PROSPECTOR_CAMPAIGN` | empty, or `contracting_tree_removal` / `web_design` | Empty uses the config default, which is contracting and tree removal. |
| `PROSPECTOR_AREAS` | empty, or `brampton,prince_edward_county` | Empty uses both default areas. |
| `PROSPECTOR_MAX_LEADS` | `20` is the config default | Keep it modest. Public Overpass will time out on huge pulls. This cap is the OpenStreetMap insert cap. Each extra source has its own `max_per_run`. |
| `SCORE_DEAD_THRESHOLD` | `4` | Scores at or below this become `dead`. |
| `LLM_PROVIDER` | leave unset, or `anthropic` | `openai_compatible` points the OpenAI SDK at another host. |
| `LLM_MODEL` | leave unset | Defaults to `claude-haiku-5-5` for Anthropic. Required for any other provider. |
| `LLM_BASE_URL` | leave unset | Preset name or full URL. See the table below. |

## Switching the model

The default is Claude Haiku (`claude-haiku-5-5`) through the Anthropic SDK. Leave `LLM_PROVIDER` unset and keep `ANTHROPIC_API_KEY`. No new secret is required.

To use another host, set `LLM_PROVIDER=openai_compatible`, set `LLM_MODEL` to that host's model id, set `LLM_API_KEY`, and set `LLM_BASE_URL` to a preset or a full URL.

| Preset | Base URL |
| --- | --- |
| `openai` | `https://api.openai.com/v1` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `groq` | `https://api.groq.com/openai/v1` |
| `together` | `https://api.together.ai/v1` |
| `mistral` | `https://api.mistral.ai/v1` |
| `xai` | `https://api.x.ai/v1` |
| `deepseek` | `https://api.deepseek.com` |
| `openrouter` | `https://openrouter.ai/api/v1` |
| `ollama` | `http://localhost:11434/v1` |

Haiku uses adaptive thinking at effort `low`. OpenAI-compatible hosts are asked for `reasoning_effort=none`, then `minimal` if the host rejects `none`. A JSON reply that does not parse is retried once. The key is not written to the log.

Example for a local Ollama model:

```bash
LLM_PROVIDER=openai_compatible
LLM_MODEL=llama3.2
LLM_BASE_URL=ollama
LLM_API_KEY=ollama
```

Clear `LLM_PROVIDER` to go back to Haiku.

GitHub Actions cannot reach Proton Mail Bridge on your laptop. For a run inside Actions, use Proton SMTP submission (`smtp.protonmail.ch`, port 587, STARTTLS) and, if you want reply detection there, Proton IMAP. Use the Bridge when you run the pipeline on your own machine.

The scheduled job does not send to real prospects unless `TEST_MODE` is exactly `false`, `0`, `off`, or `no` and the mailbox settings are present. A manual run with "Dry run" checked never sends.

The lead database is restored from the newest `fire-sale-db-<os>-<run id>` cache and saved again under the current run id. An older cache key without a run id is not used.

## Local test mode

```bash
cp .env.example .env
```

Fill `ANTHROPIC_API_KEY` (or `LLM_API_KEY`), `EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD`, `SMTP_HOST`, `SMTP_PORT`, `TEST_RECIPIENT`, and `PROSPECTOR_CONTACT_EMAIL`. Leave `TEST_MODE=true`. Leave `LLM_PROVIDER` unset to stay on Claude Haiku.

Preview without writing leads or sending mail:

```bash
python backend/agents/director_agent.py --dry-run --max-leads 8
```

One campaign and one area, still redirected to you:

```bash
python backend/agents/director_agent.py \
  --campaign web_design \
  --areas brampton \
  --max-leads 8
```

The other combinations:

```bash
python backend/agents/director_agent.py --campaign contracting_tree_removal --areas brampton
python backend/agents/director_agent.py --campaign contracting_tree_removal --areas prince_edward_county
python backend/agents/director_agent.py --campaign web_design --areas prince_edward_county
```

Mississauga is not a default. It still works as a one-off city:

```bash
python backend/agents/prospector_agent.py --cities Mississauga --campaign web_design --max 5
```

Mark a reply by hand when IMAP is not configured:

```bash
python backend/status_update.py --email prospect@example.com --status replied
```

## What to expect

- The prospector inserts businesses for the selected campaign and tags each row with `campaign` and `area` (`brampton` or `prince_edward_county`).
- Contracting leads are property managers, real estate offices, builders, landscapers, golf courses, campgrounds, rentals, wineries, farms, churches, cemeteries, schools, and municipal offices. Other tree companies are skipped. Chains are skipped.
- Website-design leads prefer places with no website in OpenStreetMap. Phone and address are kept when email is missing.
- A run that inserts 0 leads prints a `::warning::` and a job summary. The job stays green.
- Without a key for the selected provider (`LLM_API_KEY`, or `ANTHROPIC_API_KEY` when the provider is Anthropic), scoring and copy are skipped with a warning.
- Without `TEST_RECIPIENT` while test mode is on, outreach refuses to send.
- In test mode the subject looks like `[TEST for prospect@example.com] ...` and the body names the person who was not emailed.
- Follow-ups do not go to a lead whose status is `replied`.
- Copy sells the offer on that lead: tree removal and contracting, or website design. It does not pitch a generic lead-generation service.

Change the offer, the pitch, or the service-area sentence in `config/prospecting.toml`. That file is what the prompts read.

## Extra sources on a test run

No new secret is required. Details are in [Lead sources](lead-sources.md).

- Need-help posts, permits, and directory pages are stored only. They are not messaged.
- Storm alerts show up in the job summary. `notify` in the config is `false`, so a test run does not send the weather heads-up. Turning it on still uses test mode and `TEST_RECIPIENT`.
- Quote pages under `docs/quote/` download a JSON file. Import it with `python backend/agents/quote_ingest.py --json quote.json`. That row is consent to reply, including when the sender is a homeowner.
- Paste a Facebook post with `python backend/agents/facebook_manual.py`. The group list is not fetched.
- `python backend/agents/direct_mail_export.py` writes permit addresses and leaves out email addresses.
- A consumer lead is not cold-emailed. Outreach logs it as blocked.
