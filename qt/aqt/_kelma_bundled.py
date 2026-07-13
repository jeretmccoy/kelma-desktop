# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
"""KelmaDesktop: install the bundled Kelma add-on into the user's add-ons folder
on startup, so a fresh install syncs to KelmaSync out of the box.

The add-on ships inside the app's compiled ``_aqt/data/bundled_addons/kelma``
resource folder. On each run
we copy it into ``addons21/kelma`` when the bundled version changes, *preserving*
the user's ``meta.json`` (their credentials / config) so upgrades never clobber
settings. Everything is best-effort — a failure must never block startup.
"""

from __future__ import annotations

import os
import shutil

BUNDLED_VERSION = "1.0.116"
ADDON = "kelma"
_MARKER = ".kelma_bundled_version"

# PyOxidizer compiles Python files under `_aqt/data` to `.pyc`. Anki's add-on
# discovery deliberately requires a source `__init__.py`, so a packaged bundle
# containing only `__init__.pyc` is invisible. Write this small stable loader
# after each bundle copy; the rest of the add-on can remain compiled bytecode.
_ADDON_LOADER = '''"""KelmaDesktop bundled KelmaSync loader."""
from aqt import gui_hooks

_initialized = False


def _on_profile_open(*_args):
    global _initialized
    if _initialized:
        return
    try:
        from aqt import mw
        if mw is None or mw.col is None:
            return
        from .kelma import gui
        gui.setup()
        _initialized = True
    except Exception as err:
        from aqt.utils import showWarning
        showWarning(f"KelmaSync failed to initialize:\\n{err}")


gui_hooks.profile_did_open.append(_on_profile_open)
gui_hooks.main_window_did_init.append(_on_profile_open)
'''


def _bundled_dir() -> str:
    # In source, this data starts at qt/aqt/data; the build copies it to the
    # compiled `_aqt/data` package. Resolving relative to this Python module
    # (`aqt/_kelma_bundled.py`) therefore works in source but fails in packaged
    # apps. Use Anki's canonical packaged-data resolver instead.
    from aqt.utils import aqt_data_folder

    return os.path.join(aqt_data_folder(), "bundled_addons", ADDON)


def sync_bundled_addon(mw) -> None:
    try:
        src = _bundled_dir()
        if not os.path.isdir(src):
            raise FileNotFoundError(f"bundled Kelma add-on not found at {src}")
        dst = mw.addonManager.addonsFolder(ADDON)
        marker = os.path.join(dst, _MARKER)

        if os.path.isdir(dst):
            try:
                with open(marker, encoding="utf8") as f:
                    if f.read().strip() == BUNDLED_VERSION:
                        return  # already current
            except OSError:
                pass  # missing/unreadable marker -> (re)install

        os.makedirs(dst, exist_ok=True)
        # Copy code + default config, but never overwrite the user's meta.json.
        for root, _dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            target_root = dst if rel == "." else os.path.join(dst, rel)
            os.makedirs(target_root, exist_ok=True)
            for name in files:
                if name == "meta.json":
                    continue
                shutil.copy2(os.path.join(root, name), os.path.join(target_root, name))

        # Always refresh the source bootstrap; it is app code, not user config.
        with open(os.path.join(dst, "__init__.py"), "w", encoding="utf8") as f:
            f.write(_ADDON_LOADER)

        with open(marker, "w", encoding="utf8") as f:
            f.write(BUNDLED_VERSION)
    except Exception as err:  # noqa: BLE001 - never break startup
        # Do not leave a fresh Desktop install silently falling through to
        # Anki's native login again. Report the packaging/install failure while
        # still allowing the rest of the app to open.
        print(f"Kelma bundled add-on installation failed: {err}")
        try:
            from aqt.utils import showWarning

            showWarning(f"KelmaSync could not be installed:\n{err}")
        except Exception:
            pass


def run_kelma_sync(mw) -> None:
    """Run KelmaSync from Desktop core without any native AnkiWeb fallback."""
    try:
        import importlib

        addon = importlib.import_module(ADDON)
        initialize = getattr(addon, "_on_profile_open", None)
        if initialize:
            initialize()
        gui = importlib.import_module(f"{ADDON}.kelma.gui")
        gui.run_kelma_desktop_sync()
    except Exception as err:  # noqa: BLE001 - show actionable startup failure
        from aqt.utils import showWarning

        showWarning(f"KelmaSync could not start:\n{err}", parent=mw)
