from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.infrastructure.storage_entrypoint import (
    chown_tree,
    main,
    prepare_storage,
    storage_path_from_env,
)


def test_storage_path_from_env_defaults_to_data_storage() -> None:
    assert storage_path_from_env({}) == Path("/data/storage")
    assert storage_path_from_env({"STORAGE_PATH": "  "}) == Path("/data/storage")
    assert storage_path_from_env({"STORAGE_PATH": "/data/jobs"}) == Path("/data/jobs")


def test_prepare_storage_rejects_filesystem_root() -> None:
    with pytest.raises(SystemExit, match="must not be /"):
        prepare_storage(Path("/"), uid=os.getuid(), gid=os.getgid())


def test_prepare_storage_creates_and_chowns_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, int, int]] = []

    def fake_lchown(path: str | bytes | os.PathLike[str], uid: int, gid: int) -> None:
        calls.append((str(path), uid, gid))

    monkeypatch.setattr(os, "lchown", fake_lchown)
    root = tmp_path / "storage"
    nested = root / "jobs" / "x"
    nested.mkdir(parents=True)
    (nested / "status.json").write_text("{}", encoding="utf-8")
    prepare_storage(root, uid=1000, gid=1000)
    owned = {Path(path).name for path, uid, gid in calls if uid == 1000 and gid == 1000}
    assert "storage" in owned
    assert "jobs" in owned
    assert "status.json" in owned


def test_chown_tree_does_not_follow_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "secret"
    outside.write_text("nope", encoding="utf-8")
    root = tmp_path / "storage"
    root.mkdir()
    link = root / "link"
    link.symlink_to(outside)
    seen: list[str] = []

    def fake_lchown(path: str | bytes | os.PathLike[str], uid: int, gid: int) -> None:
        seen.append(os.fspath(path))

    monkeypatch.setattr(os, "lchown", fake_lchown)
    chown_tree(root, uid=1, gid=1)
    assert str(outside) not in seen
    assert str(link) in seen


def test_main_requires_a_command() -> None:
    with pytest.raises(SystemExit, match="missing command"):
        main([])


def test_main_skips_chown_when_not_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    recorded: dict[str, object] = {}

    def fake_execvp(file: str, args: list[str]) -> None:
        recorded["file"] = file
        recorded["args"] = list(args)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvp", fake_execvp)
    with pytest.raises(SystemExit) as exc:
        main(["uvicorn", "app.main:app"])
    assert exc.value.code == 0
    assert recorded["file"] == "uvicorn"
    assert recorded["args"] == ["uvicorn", "app.main:app"]


def test_main_prepares_storage_and_drops_when_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    pw = SimpleNamespace(pw_name="app", pw_uid=1000, pw_gid=1000, pw_dir="/app")
    monkeypatch.setattr("app.infrastructure.storage_entrypoint.pwd.getpwnam", lambda name: pw)
    calls: dict[str, object] = {}
    monkeypatch.setattr(os, "lchown", lambda path, uid, gid: None)
    monkeypatch.setattr(
        os,
        "initgroups",
        lambda name, gid: calls.update(initgroups=(name, gid)),
    )
    monkeypatch.setattr(os, "setgid", lambda gid: calls.update(setgid=gid))
    monkeypatch.setattr(os, "setuid", lambda uid: calls.update(setuid=uid))

    def fake_execvp(file: str, args: list[str]) -> None:
        calls["exec"] = (file, list(args))
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvp", fake_execvp)
    with pytest.raises(SystemExit):
        main(["python", "-m", "app.workers"])
    assert calls["setuid"] == 1000
    assert calls["setgid"] == 1000
    assert calls["initgroups"] == ("app", 1000)
    assert calls["exec"] == ("python", ["python", "-m", "app.workers"])
    assert (tmp_path / "storage").is_dir()
