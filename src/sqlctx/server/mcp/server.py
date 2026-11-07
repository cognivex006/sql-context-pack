"""Streamable HTTP MCP tools and resources over the shared application facade."""

from __future__ import annotations

import inspect
import json
import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from sqlctx.application.pull import PullRequest, PullStatus
from sqlctx.context_index.contracts import (
    ContextIndexListRequest,
    ContextIndexPage,
)
from sqlctx.core.enums import ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.routine_deploy.contracts import (
    RoutineApplyResult,
    RoutineDeploymentPlan,
    RoutinePlanRequest,
)
from sqlctx.security.audit import OperationAuditLogger
from sqlctx.server.contracts import (
    ConnectionTestResult,
    QueryDataRequest,
    QueryDataResult,
    ServiceStatusResponse,
    ValueMode,
)
from sqlctx.server.facade import ServiceFacade


class McpPublicError(Exception):
    """Structured, sanitized error text retained by FastMCP's isError response."""

    def __init__(self, error: SqlCtxError) -> None:
        super().__init__(
            json.dumps({"error": error.public_payload()}, sort_keys=True, ensure_ascii=False)
        )


def _json(value: Any) -> Any:
    return value.model_dump(mode="json", by_alias=True) if isinstance(value, BaseModel) else value


class McpToolRouter:
    """SDK-independent dispatcher used by MCP registration and parity tests."""

    def __init__(self, service: ServiceFacade, caller: str) -> None:
        self.service = service
        self.caller = caller
        self.audit = OperationAuditLogger(service.state) if hasattr(service, "state") else None

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        started = time.perf_counter()
        error_code: str | None = None
        result: Any = None
        try:
            result = self._invoke(name, arguments)
            return result
        except SqlCtxError as exc:
            error_code = exc.code
            raise McpPublicError(exc) from exc
        except Exception:
            error_code = "INTERNAL_ERROR"
            raise
        finally:
            if self.audit is not None:
                query_result = (
                    result if name == "sqlctx_query_data" and isinstance(result, dict) else {}
                )
                requested_mode = arguments.get("value_mode", "short")
                self.audit.record(
                    transport="mcp",
                    caller=self.caller,
                    operation=name,
                    outcome="failed" if error_code else "succeeded",
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    error_code=error_code,
                    value_mode=(
                        requested_mode
                        if name == "sqlctx_query_data" and requested_mode in {"short", "full"}
                        else None
                    ),
                    returned_row_count=(
                        int(query_result.get("returned_row_count", 0))
                        if name == "sqlctx_query_data"
                        else None
                    ),
                    truncated=(
                        bool(query_result.get("truncated", False))
                        if name == "sqlctx_query_data"
                        else None
                    ),
                )

    def _invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        s = self.service
        if name == "sqlctx_get_status":
            return _json(s.get_status())
        if name == "sqlctx_test_profile":
            return s.test_profile(str(arguments["profile"]))
        if name == "sqlctx_query_data":
            return _json(s.query(QueryDataRequest.model_validate(arguments)))
        if name == "sqlctx_list_context_index":
            return _json(s.list_context_index(ContextIndexListRequest.model_validate(arguments)))
        if name == "sqlctx_plan_routine_deployment":
            return _json(
                s.plan_routine_deployment(
                    RoutinePlanRequest.model_validate(arguments), caller=self.caller
                )
            )
        if name == "sqlctx_apply_routine_deployment":
            return _json(s.apply_routine_deployment(str(arguments["plan_id"]), caller=self.caller))
        if name == "sqlctx_pull":
            return _json(s.pulls.start(PullRequest.model_validate(arguments), caller=self.caller))
        if name == "sqlctx_wait_pull":
            return _json(
                s.pulls.wait(
                    str(arguments["pull_id"]),
                    timeout_seconds=float(arguments.get("timeout_seconds", 50)),
                )
            )
        raise ValueError("unknown MCP tool")


def build_mcp(service: ServiceFacade, caller: str) -> Any:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "sql-context-pack", stateless_http=True, json_response=True, streamable_http_path="/mcp"
    )
    router = McpToolRouter(service, caller)

    def tool(name: str):  # type: ignore[no-untyped-def]
        register = mcp.tool(name=name, structured_output=True)

        def decorate(fn):  # type: ignore[no-untyped-def]
            # Python 3.13 dedents docstrings at compile time; 3.11 does not. FastMCP takes
            # the tool description straight from __doc__, so without normalizing here the
            # description shipped to clients -- and the generated contract built from it --
            # would differ by interpreter version.
            if fn.__doc__:
                fn.__doc__ = inspect.cleandoc(fn.__doc__)
            return register(fn)

        return decorate

    @tool("sqlctx_get_status")
    def get_status() -> ServiceStatusResponse:
        """Return capabilities, safe profiles, registered folder IDs and SQLFluff readiness.

        Read-only; probes no database. Use it to pick the profile and folder ID for sqlctx_pull.
        """
        return router.invoke("sqlctx_get_status", {})

    @tool("sqlctx_test_profile")
    def test_profile(profile: str) -> ConnectionTestResult:
        """Perform a bounded read-only connection test for a named profile."""
        return router.invoke("sqlctx_test_profile", {"profile": profile})

    @tool("sqlctx_query_data")
    def query_data(
        sql: Annotated[str, Field(min_length=1, max_length=20_000)],
        profile: str,
        max_rows: Annotated[int, Field(ge=1, le=500)] = 100,
        value_mode: ValueMode = "short",
    ) -> QueryDataResult:
        """Run one validated read-only relational SELECT and return masked Markdown."""
        return router.invoke(
            "sqlctx_query_data",
            {
                "sql": sql,
                "profile": profile,
                "max_rows": max_rows,
                "value_mode": value_mode,
            },
        )

    @tool("sqlctx_list_context_index")
    def list_context_index(
        profile: str,
        context: str | None = None,
        object_type: ObjectType | None = None,
        tag: str | None = None,
        status: Literal["confirmed", "unresolved"] | None = None,
        active: bool = True,
        cursor: str | None = None,
        limit: Annotated[int, Field(ge=1, le=250)] = 100,
    ) -> ContextIndexPage:
        """List bounded DB_METADATA_CONTEXT metadata without SQL bodies or credentials."""
        return router.invoke(
            "sqlctx_list_context_index",
            {
                "profile": profile,
                "context": context,
                "object_type": object_type,
                "tag": tag,
                "status": status,
                "active": active,
                "cursor": cursor,
                "limit": limit,
            },
        )

    @tool("sqlctx_plan_routine_deployment")
    def plan_routine_deployment(
        profile: str,
        folder_id: str,
        idempotency_key: Annotated[str, Field(min_length=8, max_length=128)],
        relative_path: str | None = None,
        stop_on_error: bool = True,
    ) -> RoutineDeploymentPlan:
        """Plan one managed routine file or every eligible file in a registered folder."""
        return router.invoke(
            "sqlctx_plan_routine_deployment",
            {
                "profile": profile,
                "folder_id": folder_id,
                "relative_path": relative_path,
                "stop_on_error": stop_on_error,
                "idempotency_key": idempotency_key,
            },
        )

    @tool("sqlctx_apply_routine_deployment")
    def apply_routine_deployment(plan_id: str) -> RoutineApplyResult:
        """Apply an unchanged SQL Server routine plan after owner approval."""
        return router.invoke("sqlctx_apply_routine_deployment", {"plan_id": plan_id})

    @tool("sqlctx_pull")
    def pull(
        profile: str,
        folder_id: str,
        schemas: list[str] | None = None,
        object_types: list[ObjectType] | None = None,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        sample_rows_per_table: Annotated[int | None, Field(ge=10, le=20)] = None,
        actor_id: Annotated[int | None, Field(gt=0)] = None,
        session_cache_key: str | None = None,
    ) -> PullStatus:
        """Export context into a registered folder as one job; returns all blockers at once.

        Start here for "export/pull these objects into this folder". Omit include_patterns for
        every permitted object. Status `blocked` lists every blocker with its owner remedy and
        starts nothing; otherwise call sqlctx_wait_pull until the status is terminal.
        """
        return router.invoke(
            "sqlctx_pull",
            {
                "profile": profile,
                "folder_id": folder_id,
                "schemas": schemas,
                "object_types": object_types,
                "include_patterns": include_patterns or [],
                "exclude_patterns": exclude_patterns or [],
                "sample_rows_per_table": sample_rows_per_table,
                "actor_id": actor_id,
                "session_cache_key": session_cache_key,
            },
        )

    @tool("sqlctx_wait_pull")
    def wait_pull(
        pull_id: str, timeout_seconds: Annotated[float, Field(ge=0, le=50)] = 50
    ) -> PullStatus:
        """Wait up to 50 s for a pull job to change phase or progress, then return its status."""
        return router.invoke(
            "sqlctx_wait_pull", {"pull_id": pull_id, "timeout_seconds": timeout_seconds}
        )

    @mcp.resource("sqlctx://export/{export_id}/manifest", mime_type="application/json")
    def manifest_resource(export_id: str) -> str:
        """Read the same structured export manifest exposed by HTTP."""
        import json

        return json.dumps(service.exports.manifest(export_id), sort_keys=True)

    @mcp.resource("sqlctx://export/{export_id}/report", mime_type="application/json")
    def report_resource(export_id: str) -> str:
        """Read the same structured export report exposed by HTTP."""
        import json

        return json.dumps(_json(service.exports.report(export_id)), sort_keys=True)

    # FastMCP's generated argument models default to ignoring unknown top-level keys.
    # The v1 contract is stricter: advertise and enforce additionalProperties=false.
    for registered in mcp._tool_manager.list_tools():
        registered.fn_metadata.arg_model.model_config["extra"] = "forbid"
        registered.fn_metadata.arg_model.model_rebuild(force=True)
        registered.parameters = registered.fn_metadata.arg_model.model_json_schema(by_alias=True)

    return mcp
