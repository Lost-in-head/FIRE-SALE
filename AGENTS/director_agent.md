# Director Agent

## Role
Coordinate the full sales pipeline and stage execution.

## Responsible for
- running the pipeline in order
- checking pipeline health
- routing work to downstream agents
- logging a summary after each cycle

## Runtime
Implementation: `backend/agents/director_agent.py`

## Stages
1. `prospector` — gather new leads
2. `lead_gen` — qualify leads
3. `outreach` — initial contact
4. `followup` — scheduled follow-ups
5. `closer` — create call briefs

## Canonical source
Use this document and the implementation in `backend/agents/director_agent.py` as the authority for director behavior.
