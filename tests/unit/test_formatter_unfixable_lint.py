"""SQLFluff exit 1 for remaining unfixable lint still yields the formatted file."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from sqlctx.core.enums import FormatStatus
from sqlctx.formatting.formatter import SqlFluffFormatter, _unfixable_only
from sqlctx.formatting.manager import SqlFluffManager
from sqlctx.security.runtime import JsonRuntimeStateStore

UNFIXABLE_ONLY = (
    "==== finding fixable violations ====\n"
    "== [object.sql] FAIL\nL:  21 | P:   5 | LT08 | Blank line expected\n"
    "==== no fixable linting violations found ====\nAll Finished!\n"
    "  [3 unfixable linting violations found]\n"
)
UNPARSABLE = (
    "==== finding fixable violations ====\n"
    "  [1 templating/parsing errors found]\n  [2 unfixable linting violations found]\n"
)


class Runner:
    def __init__(self, format_code: int, format_stdout: str, verify_code: int = 0) -> None:
        self.format_code = format_code
        self.format_stdout = format_stdout
        self.verify_code = verify_code
        self.parses = 0

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[1] == "-c":
            return subprocess.CompletedProcess(command, 0, "[3, 11, 10]\n", "")
        if command[1:4] == ["-m", "sqlfluff", "parse"]:
            self.parses += 1
            code = self.verify_code if self.parses > 1 else 0
            return subprocess.CompletedProcess(command, code, "", "")
        if command[1:4] == ["-m", "sqlfluff", "format"]:
            Path(command[-1]).write_text("SELECT\n    1;\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, self.format_code, self.format_stdout, "")
        return subprocess.CompletedProcess(command, 0, "", "")


def _format(tmp_path: Path, runner: Runner) -> object:
    manager = SqlFluffManager(
        JsonRuntimeStateStore(tmp_path / "runtime"),
        python_executable=Path(sys.executable),
        runner=runner,
    )
    return SqlFluffFormatter(manager, runner=runner).format_one(
        object_id="procedure:dbo.p", sql="select 1;", dialect="tsql"
    )


def test_remaining_unfixable_lint_keeps_the_formatted_file(tmp_path: Path) -> None:
    result = _format(tmp_path, Runner(1, UNFIXABLE_ONLY))
    assert result.status == FormatStatus.FORMATTED  # type: ignore[attr-defined]
    assert result.content == "SELECT\n    1;\n"  # type: ignore[attr-defined]
    assert result.diagnostics == ["unfixable_lint_violations_remaining: 3"]  # type: ignore[attr-defined]


def test_a_formatted_file_that_no_longer_parses_is_still_rolled_back(tmp_path: Path) -> None:
    result = _format(tmp_path, Runner(1, UNFIXABLE_ONLY, verify_code=1))
    assert result.status == FormatStatus.ROLLED_BACK  # type: ignore[attr-defined]
    assert result.content == "select 1;"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("code", "stdout"),
    [
        (1, UNPARSABLE),
        (1, "Fix aborted due to unparsable template variables."),
        (2, UNFIXABLE_ONLY),
        (1, ""),
    ],
)
def test_other_format_failures_still_preserve_the_original(
    tmp_path: Path, code: int, stdout: str
) -> None:
    result = _format(tmp_path, Runner(code, stdout))
    assert result.status == FormatStatus.FORMAT_FAILED  # type: ignore[attr-defined]
    assert result.content == "select 1;"  # type: ignore[attr-defined]


def test_unfixable_only_reads_the_count() -> None:
    done = subprocess.CompletedProcess(["sqlfluff"], 1, UNFIXABLE_ONLY, "")
    assert _unfixable_only(done) == 3
    assert _unfixable_only(subprocess.CompletedProcess(["sqlfluff"], 0, UNFIXABLE_ONLY, "")) is None
