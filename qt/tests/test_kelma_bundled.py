from types import SimpleNamespace

from aqt import _kelma_bundled


def test_bundle_upgrade_removes_stale_source_and_preserves_metadata(
    tmp_path, monkeypatch
) -> None:
    src = tmp_path / "bundle"
    dst = tmp_path / "addons21" / "kelma"
    (src / "kelma" / "kelma_sync_v2").mkdir(parents=True)
    (src / "kelma" / "consts.pyc").write_bytes(b"new bytecode")
    (src / "kelma" / "kelma_sync_v2" / "conflict_policy.pyc").write_bytes(b"new policy")
    (src / "kelma" / "kelma_sync_v2" / "review_sync.pyc").write_bytes(b"review history")
    (src / "meta.json").write_text("bundled metadata", encoding="utf8")

    (dst / "kelma").mkdir(parents=True)
    (dst / "kelma" / "consts.py").write_text("stale source", encoding="utf8")
    (dst / "removed_module.py").write_text("stale module", encoding="utf8")
    (dst / "meta.json").write_text("user metadata", encoding="utf8")
    (dst / ".kelma_bundled_version").write_text("1.0.118", encoding="utf8")

    monkeypatch.setattr(_kelma_bundled, "_bundled_dir", lambda: str(src))
    manager = SimpleNamespace(addonsFolder=lambda _addon: str(dst))
    mw = SimpleNamespace(addonManager=manager)

    _kelma_bundled.sync_bundled_addon(mw)

    assert not (dst / "kelma" / "consts.py").exists()
    assert not (dst / "removed_module.py").exists()
    assert (dst / "kelma" / "consts.pyc").read_bytes() == b"new bytecode"
    assert (
        dst / "kelma" / "kelma_sync_v2" / "conflict_policy.pyc"
    ).read_bytes() == b"new policy"
    assert (
        dst / "kelma" / "kelma_sync_v2" / "review_sync.pyc"
    ).read_bytes() == b"review history"
    assert (dst / "meta.json").read_text(encoding="utf8") == "user metadata"
    assert (dst / ".kelma_bundled_version").read_text(
        encoding="utf8"
    ) == _kelma_bundled.BUNDLED_VERSION
    assert "KelmaDesktop bundled KelmaSync loader" in (dst / "__init__.py").read_text(
        encoding="utf8"
    )
    assert not (tmp_path / "addons21" / "kelma.kelma-bundled-backup").exists()
    assert not list((tmp_path / "addons21").glob(".kelma-bundled-*"))
