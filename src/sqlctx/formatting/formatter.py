"""One-file-at-a-time, fail-isolated SQLFluff formatting."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from sqlctx.core.enums import FormatStatus
from sqlctx.core.models import HostPythonToolingDescriptor, SqlFormatResult
from sqlctx.formatting.manager import Runner, SqlFluffManager
from sqlctx.formatting.rules import FORMAT_RULE_POLICY, excluded_rules
from sqlctx.formatting.worker import shared_runner

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|key)\s*[:=]\s*\S+"
)
# Quoted literals can carry personal data or secrets; identifiers, keywords, rule codes and
# line/position markers stay, so a failure can still be located and reproduced.
_QUOTED_LITERAL = re.compile(r"N?'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")


def _redact_literal(match: re.Match[str]) -> str:
    return f"'<redacted {len(match.group(0))} chars>'"


def _sanitized_diagnostics(result: subprocess.CompletedProcess[str]) -> list[str]:
    raw = (result.stderr or result.stdout or "").strip()
    if not raw:
        return []
    text = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", raw)
    text = _QUOTED_LITERAL.sub(_redact_literal, text)
    text = text.replace("\r", " ").replace("\n", " ")
    # The fingerprint ties this bounded summary to the unredacted output, which the owner
    # reproduces locally with `sqlctx format --file <object>.sql --dialect <dialect>`.
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    lines = [text[:512]]
    if len(text) > 512:
        lines.append(f"diagnostic truncated: showing 512 of {len(text)} characters")
    lines.append(f"diagnostic fingerprint: {fingerprint}")
    return lines


_UNFIXABLE = re.compile(r"\[(\d+) unfixable linting violations found\]")
_UNPARSABLE = re.compile(r"(?i)templating/parsing errors|unparsable|fix aborted")


def _unfixable_only(result: subprocess.CompletedProcess[str]) -> int | None:
    """Count of remaining unfixable lint violations when that is the only reason for exit 1.

    ``sqlfluff format`` writes every fix it can make and then exits 1 if any violation it cannot
    fix remains. That output is formatted SQL, verified by the parse that follows; discarding it
    left most real objects unformatted. Any other failure, including parse or templating errors
    (whose fixes SQLFluff discards), returns ``None`` and is still a formatting failure.
    """
    if result.returncode != 1:
        return None
    output = "\n".join((result.stdout or "", result.stderr or ""))
    match = _UNFIXABLE.search(output)
    if match is None or _UNPARSABLE.search(output):
        return None
    return int(match.group(1))


class SqlFluffFormatter:
    def __init__(self, manager: SqlFluffManager, runner: Runner | None = None) -> None:
        self.manager = manager
        # Long-lived workers run the same SQLFluff commands without an interpreter launch each.
        self.runner = runner if runner is not None else shared_runner()

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return self.runner([str(self.manager.python_executable), *arguments])

    def format_one(
        self,
        *,
        object_id: str,
        sql: str,
        dialect: str,
        tooling: HostPythonToolingDescriptor | None = None,
    ) -> SqlFormatResult:
        descriptor = tooling or self.manager.status()
        fingerprint = descriptor.tooling_fingerprint or "unavailable"
        version = descriptor.sqlfluff_version or self.manager.pinned_version
        cache_payload = {
            "sql": sql,
            "dialect": dialect,
            "tooling_fingerprint": fingerprint,
            "policy": f"parse-format-verify-v3|{FORMAT_RULE_POLICY}",
        }
        cache_key = hashlib.sha256(
            json.dumps(cache_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self.manager.state.read_json(f"format-cache/{cache_key}.json")
        if isinstance(cached, dict):
            result = SqlFormatResult.model_validate(cached)
            return result.model_copy(update={"object_id": object_id, "cache_hit": True})

        def finish(result: SqlFormatResult) -> SqlFormatResult:
            self.manager.state.write_json(
                f"format-cache/{cache_key}.json", result.model_dump(mode="json")
            )
            return result

        with tempfile.TemporaryDirectory(prefix="sqlctx-format-") as temporary:
            sql_path = Path(temporary) / "object.sql"
            sql_path.write_text(sql, encoding="utf-8", newline="\n")
            parse = self._run(
                [
                    "-m",
                    "sqlfluff",
                    "parse",
                    "--dialect",
                    dialect,
                    "--templater",
                    "raw",
                    str(sql_path),
                ]
            )
            if parse.returncode != 0:
                return finish(
                    SqlFormatResult(
                        object_id=object_id,
                        status=FormatStatus.PARSE_FAILED,
                        content=sql,
                        diagnostics=_sanitized_diagnostics(parse),
                        sqlfluff_version=version,
                        tooling_fingerprint=fingerprint,
                    )
                )
            formatted = self._run(
                [
                    "-m",
                    "sqlfluff",
                    "format",
                    "--exclude-rules",
                    ",".join(excluded_rules(dialect)),
                    "--dialect",
                    dialect,
                    "--templater",
                    "raw",
                    str(sql_path),
                ]
            )
            remaining = _unfixable_only(formatted)
            if formatted.returncode != 0 and remaining is None:
                return finish(
                    SqlFormatResult(
                        object_id=object_id,
                        status=FormatStatus.FORMAT_FAILED,
                        content=sql,
                        diagnostics=_sanitized_diagnostics(formatted),
                        sqlfluff_version=version,
                        tooling_fingerprint=fingerprint,
                    )
                )
            candidate = sql_path.read_text(encoding="utf-8")
            verify = self._run(
                [
                    "-m",
                    "sqlfluff",
                    "parse",
                    "--dialect",
                    dialect,
                    "--templater",
                    "raw",
                    str(sql_path),
                ]
            )
            if verify.returncode != 0:
                return finish(
                    SqlFormatResult(
                        object_id=object_id,
                        status=FormatStatus.ROLLED_BACK,
                        content=sql,
                        diagnostics=_sanitized_diagnostics(verify),
                        sqlfluff_version=version,
                        tooling_fingerprint=fingerprint,
                    )
                )
            return finish(
                SqlFormatResult(
                    object_id=object_id,
                    status=FormatStatus.FORMATTED,
                    content=candidate,
                    diagnostics=(
                        [f"unfixable_lint_violations_remaining: {remaining}"]
                        if remaining is not None
                        else []
                    ),
                    sqlfluff_version=version,
                    tooling_fingerprint=fingerprint,
                )
            )
