"""Director stage order, including the prospector as the first stage."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGE_ORDER = ["prospector", "lead_gen", "outreach", "replies", "followup", "closer"]


@pytest.fixture()
def director(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    monkeypatch.setenv("DRY_RUN", "true")
    import importlib
    import db as db_module
    import agents.director_agent as director_module

    importlib.reload(db_module)
    importlib.reload(director_module)
    db_module.init_db()
    return director_module


def test_builtin_stage_order(director):
    assert list(director.STAGES) == STAGE_ORDER


def _patch_stages(director):
    order = []
    for name in list(director.STAGES):
        def make(stage_name=name):
            def run_stage():
                order.append(stage_name)
                return {"ok": True}
            return run_stage
        director.STAGES[name] = make()
    return order


def test_full_run_invokes_stages_in_order(director):
    order = _patch_stages(director)
    director.DirectorAgent().run()
    assert order == STAGE_ORDER


def test_prospector_stage_runs_alone(director):
    order = _patch_stages(director)
    director.DirectorAgent(stage="prospector").run()
    assert order == ["prospector"]


def test_single_stage_missing_config_does_not_crash(director):
    def missing_key():
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    director.STAGES["lead_gen"] = missing_key
    director.DirectorAgent(stage="lead_gen").run()


def test_dry_run_flag_is_visible_when_the_prospector_stage_starts(director, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    seen = {}

    def run_stage():
        from agents.prospecting_config import plan_from_env
        seen["dry_run"] = plan_from_env().dry_run
        return {"ok": True}

    director.STAGES["prospector"] = run_stage
    director.DirectorAgent(dry_run=True, stage="prospector").run()
    assert seen["dry_run"] is True


def test_workflow_can_select_the_prospector_stage():
    text = (ROOT / ".github" / "workflows" / "run_pipeline.yml").read_text()
    assert "prospector" in text
    assert text.index("prospector") < text.index("lead_gen")


def test_workflow_restores_the_latest_database_and_defaults_to_test_mode():
    text = (ROOT / ".github" / "workflows" / "run_pipeline.yml").read_text()
    assert "actions/cache/restore@v4" in text
    assert "actions/cache/save@v4" in text
    assert "fire-sale-db-${{ runner.os }}-${{ github.run_id }}" in text
    assert "fire-sale-db-${{ runner.os }}-" in text
    assert "key: fire-sale-db-${{ runner.os }}\n" not in text
    assert "smtp.gmail.com" not in text
    assert "SMTP_STARTTLS" in text
    assert "TEST_MODE" in text
    assert "TEST_RECIPIENT" in text
    assert "IMAP_HOST" in text
    assert "PROSPECTOR_AREAS" in text
    assert "PROSPECTOR_CONTACT_EMAIL" in text
    assert 'export TEST_MODE=true' in text
    assert 'export TEST_MODE=false' in text
    assert "LLM_PROVIDER:" in text
    assert "LLM_MODEL:" in text
    assert "LLM_BASE_URL:" in text
    assert "secrets.LLM_API_KEY" in text
    assert "secrets.ANTHROPIC_API_KEY" in text
    assert "unset llm_key" in text
    assert 'echo "$llm_key"' not in text
    assert 'echo "$LLM_API_KEY"' not in text
    assert 'echo "$ANTHROPIC_API_KEY"' not in text


def test_zero_leads_and_missing_secrets_are_annotated(director, capsys, monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    def empty():
        return {"leads_inserted": 0, "campaign": "contracting_tree_removal", "areas": ["brampton"], "errors": []}

    director.STAGES["prospector"] = empty
    director.DirectorAgent(stage="prospector").run()
    out = capsys.readouterr().out
    assert "::warning::" in out
    assert "0 leads" in out
    assert "0 leads" in summary.read_text()

    def missing_key():
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    director.STAGES["lead_gen"] = missing_key
    director.DirectorAgent(stage="lead_gen").run()
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out
    assert "::warning::" in out


def test_legacy_nested_prospector_is_removed():
    assert not (ROOT / "FIRE-SALE-prospector").exists()
