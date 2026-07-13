from unittest.mock import patch

from aqt import _kelma_update


def test_kelma_versions_are_compared_numerically() -> None:
    assert _kelma_update.version_tuple("1.0.10") > _kelma_update.version_tuple("1.0.9")
    assert _kelma_update.version_tuple("2.0") > _kelma_update.version_tuple("1.99.99")


def test_desktop_update_selects_and_validates_platform_artifact() -> None:
    manifest = {
        "schema": 1,
        "desktop": {
            "version": "1.0.116",
            "notes_url": "https://kelma.tech/downloads",
            "platforms": {
                "windows-x86_64": {
                    "filename": "kelma-win-x64.msi",
                    "url": "https://example.com/kelma-win-x64.msi",
                    "sha256": "a" * 64,
                    "size": 123,
                }
            },
        },
    }
    with (
        patch.object(_kelma_update, "_fetch_json", return_value=manifest),
        patch.object(_kelma_update, "platform_key", return_value="windows-x86_64"),
    ):
        update = _kelma_update.fetch_desktop_update()
    assert update.version == "1.0.116"
    assert update.filename == "kelma-win-x64.msi"
    assert update.sha256 == "a" * 64
