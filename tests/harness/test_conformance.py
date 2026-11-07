from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "sqlctx_harness_conformance", ROOT / "harnesses/conformance.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("harness", ["codex", "claude", "gemini"])
def test_all_harnesses_produce_identical_safe_normalized_results(harness: str) -> None:
    baseline = MODULE.simulate("codex", ROOT)
    result = MODULE.simulate(harness, ROOT)
    assert result == baseline
    assert result["skill_discovered"]
    assert result["mcp_tool_count"] == 8
    assert result["credentials_exposed"] is False
    assert result["preview_pages_consumed"] == 2
    assert result["analysis_pages_consumed"] == 2
    assert result["full_analysis_despite_selection"] is True
    assert result["proposal_status"] == "final_suggested"
    assert result["owner_resolution_status"] == "final_confirmed"
    assert result["owner_questions"] == 1
    assert result["validation"] == "valid"


def test_only_one_canonical_skill_workflow_exists() -> None:
    skills = list(ROOT.rglob("SKILL.md"))
    assert skills == [ROOT / "skills/sql-context-pack/SKILL.md"]
    assert not (ROOT / "harnesses/codex/SKILL.md").exists()
    assert not (ROOT / "harnesses/claude/SKILL.md").exists()
    assert not (ROOT / "harnesses/gemini/SKILL.md").exists()


def test_canonical_skill_routes_to_the_eight_core_tools_only() -> None:
    import json

    skill = (ROOT / "skills/sql-context-pack/SKILL.md").read_text(encoding="utf-8")
    flat = " ".join(skill.split())
    tools = json.loads((ROOT / "docs/generated/mcp-tools.json").read_text(encoding="utf-8"))
    core = {item["name"] for item in tools["tools"]}
    assert len(core) == 8
    mentioned = set(re.findall(r"sqlctx_[a-z_]+", skill))
    assert mentioned <= core, sorted(mentioned - core)
    assert {"sqlctx_pull", "sqlctx_wait_pull", "sqlctx_query_data"} <= mentioned
    assert not (ROOT / "skills/sql-context-pack/references/workflow.md").exists()
    assert "no name or pattern attached means no `include_patterns`" in flat
    assert "Ask once only when a term is genuinely ambiguous" in flat
    assert "`ETL`" in skill
    assert "report the whole list once, exactly, and stop" in flat
    assert "Never promote a suggestion yourself" in flat
    assert "reveal_handoff" in skill
