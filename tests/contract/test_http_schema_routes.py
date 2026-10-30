from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from sqlctx.security.profiles import YamlConnectionProfileRepository
from sqlctx.security.runtime import JsonRuntimeStateStore
from sqlctx.server.facade import ServiceFacade
from sqlctx.server.http.app import create_app

ROOT = Path(__file__).resolve().parents[2]


def test_schema_and_docs_routes_are_not_served_without_a_bearer(tmp_path: Path) -> None:
    state = JsonRuntimeStateStore(tmp_path)
    facade = ServiceFacade(
        state=state,
        profiles=YamlConnectionProfileRepository(tmp_path / "profiles.yaml", {}),
        categories_path=ROOT / "config/categories.yaml",
        overrides_path=tmp_path / "category-overrides.yaml",
    )
    app = create_app(facade)

    async def statuses() -> dict[str, int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            routes = ("/api/docs", "/api/openapi.json", "/redoc", "/docs", "/openapi.json")
            result = {route: (await client.get(route)).status_code for route in routes}
            result["/api/v1/health"] = (await client.get("/api/v1/health")).status_code
            return result

    observed = asyncio.run(statuses())

    assert observed.pop("/api/v1/health") == 401
    assert set(observed.values()) == {404}
    assert app.openapi()["info"]["title"] == "SQL Context Pack"
