# FIRE-SALE

Outbound prospecting and follow-up for two services the owner sells directly: contracting and tree removal, and website design for independent local businesses.

The first runs stay in test mode. Mail is redirected to the owner's own address. Prospects are not emailed until `TEST_MODE` is explicitly turned off.

## System shape
- `backend/db.py` — database and schema migration
- `backend/agents/` — pipeline implementation
- `config/prospecting.toml` — campaigns, areas, offer text, and the chain denylist
- `AGENTS/` — agent specs

## Pipeline
Prospecting → filter existing leads → qualification → outreach → reply check → follow-up → call brief

Bad emails are cleared. Government offices, schools, places of worship, hospitals, boards of trade, and the other off-target lists in `config/prospecting.toml` are not scored or emailed. `python backend/agents/filter_existing.py` marks rows already stored; it does not delete them and it does not send email.

OpenStreetMap is one source. Need-help posts, weather alerts, Brampton permits, directories, and quote-form files are separate sources in `config/prospecting.toml`. Each one can be turned off. A source that cannot be read is skipped. See [Lead sources](docs/lead-sources.md).

Intent posts and consumer leads (homeowners, cottagers, landlords) are not cold-emailed. A quote form is consent to reply.

## Language model

Scoring, outreach copy, follow-ups, and call briefs go through `backend/llm.py`. The default is Anthropic Claude Haiku (`claude-haiku-5-5`). An existing `ANTHROPIC_API_KEY` is used when `LLM_API_KEY` is empty, so the default needs no new secret. If the key for the selected provider is missing, the director skips those stages and the run stays green.

| Setting | Variable | Default |
| --- | --- | --- |
| Provider | `LLM_PROVIDER` | `anthropic` |
| Model | `LLM_MODEL` | `claude-haiku-5-5` on Anthropic. Set this for any other provider. |
| Key | `LLM_API_KEY` | Falls back to `ANTHROPIC_API_KEY` for Anthropic only. |
| Base URL | `LLM_BASE_URL` | Anthropic's API. For `openai_compatible`, a preset name or a full URL. |

`LLM_PROVIDER=openai_compatible` uses the OpenAI SDK. `LLM_BASE_URL` presets: `openai`, `gemini`, `groq`, `together`, `mistral`, `xai`, `deepseek`, `openrouter`, `ollama`. Haiku uses adaptive thinking at effort `low`. Other hosts are asked for `reasoning_effort=none`, then `minimal` if that value is rejected.

To switch, set the provider, the model id that host expects, the base URL or preset, and `LLM_API_KEY`. Unset `LLM_PROVIDER` to return to Haiku.

## Start here
- [First internal test run](docs/first-internal-test-run.md) — secrets, local test mode, and what a run should do
- [AGENTS/README.md](AGENTS/README.md)

## Canonical docs
- [AGENTS/director_agent.md](AGENTS/director_agent.md)
- [AGENTS/prospector_agent.md](AGENTS/prospector_agent.md)
- [AGENTS/lead_gen_agent.md](AGENTS/lead_gen_agent.md)
- [AGENTS/outreach_agent.md](AGENTS/outreach_agent.md)
- [AGENTS/followup_agent.md](AGENTS/followup_agent.md)
- [AGENTS/closer_agent.md](AGENTS/closer_agent.md)
