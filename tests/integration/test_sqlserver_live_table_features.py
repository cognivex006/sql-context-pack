"""Live round-trip of SQL Server table metadata; skipped unless a profile is configured.

Set ``SQLCTX_LIVE_SQLSERVER_PROFILE`` to an owner-configured SQL Server profile name and
``SQLCTX_LIVE_SQLSERVER_TABLE`` to ``schema.table`` (both required). Only read-only catalog
metadata is queried; no row data is read and nothing is written to the database.
"""

from __future__ import annotations

import os

import pytest

from sqlctx.core.enums import DatabaseEngine

PROFILE_NAME = os.environ.get("SQLCTX_LIVE_SQLSERVER_PROFILE")
TABLE = os.environ.get("SQLCTX_LIVE_SQLSERVER_TABLE")

pytestmark = pytest.mark.skipif(
    not (PROFILE_NAME and TABLE),
    reason="set SQLCTX_LIVE_SQLSERVER_PROFILE and SQLCTX_LIVE_SQLSERVER_TABLE to run live",
)


def test_live_table_metadata_round_trip() -> None:
    from sqlctx.adapters.registry import create_adapter
    from sqlctx.security.profiles import YamlConnectionProfileRepository

    assert PROFILE_NAME and TABLE
    try:
        profile = YamlConnectionProfileRepository().resolve(PROFILE_NAME)
    except Exception as exc:  # profile store absent or not readable by this user
        pytest.skip(f"live profile unavailable: {type(exc).__name__}")
    if profile.engine != DatabaseEngine.SQLSERVER:
        pytest.skip("live profile is not SQL Server")
    adapter = create_adapter(profile.engine)
    refs = {ref.object_id: ref for ref in adapter.discover_objects(profile)}
    ref = refs.get(f"table:{TABLE}")
    if ref is None:
        pytest.skip("live table is not visible to the profile")

    obj = adapter.extract_object(profile, ref)

    assert obj.columns
    assert obj.sanitized_definition.startswith("CREATE TABLE ")
    assert all(item.feature != "probe_unavailable" for item in obj.unsupported_features), (
        "optional feature probes should be answerable by a supported server"
    )
