from __future__ import annotations

import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from sqlctx.doctor import _credential_key_findings
from sqlctx.security import key_protection
from sqlctx.security.key_protection import is_wrapped, load_master_key, unwrap


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI exists on Windows only")
def test_new_key_is_stored_machine_protected_and_opens_again(tmp_path: Path) -> None:
    path = tmp_path / "master.key"

    raw = load_master_key(path, generate=Fernet.generate_key, write=_write)

    assert is_wrapped(path.read_bytes())
    assert raw not in path.read_bytes()
    assert unwrap(path.read_bytes()) == raw
    Fernet(raw)


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI exists on Windows only")
def test_legacy_plain_key_is_upgraded_in_place_without_changing_the_key(tmp_path: Path) -> None:
    path = tmp_path / "master.key"
    legacy = Fernet.generate_key()
    path.write_bytes(legacy)
    token = Fernet(legacy).encrypt(b"existing credential")

    raw = load_master_key(path, generate=Fernet.generate_key, write=_write)

    assert raw == legacy
    assert is_wrapped(path.read_bytes())
    assert Fernet(raw).decrypt(token) == b"existing credential"


def test_a_failed_upgrade_still_opens_the_legacy_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(key_protection, "wrapping_supported", lambda: True)
    monkeypatch.setattr(key_protection, "wrap", lambda raw: b"sqlctx-dpapi-v1\n" + raw)
    path = tmp_path / "master.key"
    legacy = Fernet.generate_key()
    path.write_bytes(legacy)

    def refuse(_: Path, __: bytes) -> None:
        raise PermissionError("read-only runtime")

    assert load_master_key(path, generate=Fernet.generate_key, write=refuse) == legacy
    assert path.read_bytes() == legacy


def test_without_dpapi_keys_stay_plain_owner_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(key_protection, "wrapping_supported", lambda: False)
    path = tmp_path / "master.key"

    raw = load_master_key(path, generate=Fernet.generate_key, write=_write)

    assert path.read_bytes() == raw


def test_doctor_reports_plain_master_keys_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sqlctx.doctor.wrapping_supported", lambda: True)
    _write(tmp_path / "profile-credentials/master.key", Fernet.generate_key())
    _write(tmp_path / "master.key", b"sqlctx-dpapi-v1\nprotected")
    before = (tmp_path / "profile-credentials/master.key").read_bytes()

    findings = _credential_key_findings(tmp_path)

    assert [finding["code"] for finding in findings] == ["CREDENTIAL_KEY_UNWRAPPED"]
    assert findings[0]["evidence"] == {"unwrapped_key_count": 1}
    assert (tmp_path / "profile-credentials/master.key").read_bytes() == before


def test_doctor_is_silent_when_every_key_is_protected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sqlctx.doctor.wrapping_supported", lambda: True)
    _write(tmp_path / "master.key", b"sqlctx-dpapi-v1\nprotected")

    assert _credential_key_findings(tmp_path) == []
