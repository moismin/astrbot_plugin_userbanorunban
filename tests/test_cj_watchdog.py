import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

spec = importlib.util.spec_from_file_location("cj_watchdog_main", PLUGIN_ROOT / "main.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

CJWatchdog = module.CJWatchdog
StarTools = module.StarTools


class DummyContext:
    def __init__(self, config=None) -> None:
        self._config = config or {}

    def get_config(self, umo=None):
        return self._config

    def get_all_stars(self):
        return []

    def get_registered_star(self, name):
        return None


def _patch_data_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(StarTools, "get_data_dir", lambda plugin_name=None: tmp_path)


def test_dirty_state_file_sanitized(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    dirty_state = {
        "timeouts": {"bad": "oops", 1: -5, "ok": "2"},
        "bans": {"a": "yes", "b": "no", "c": 1, "d": 0, "e": None},
    }
    (tmp_path / "state.json").write_text(json.dumps(dirty_state), encoding="utf-8")

    plugin = CJWatchdog(DummyContext())

    assert plugin._timeouts["bad"] == 0
    assert plugin._timeouts["1"] == 0
    assert plugin._timeouts["ok"] == 2
    assert plugin._bans["a"] is True
    assert plugin._bans["b"] is False
    assert plugin._bans["c"] is True
    assert plugin._bans["d"] is False
    assert plugin._bans["e"] is False


@pytest.mark.asyncio
async def test_concurrent_save_state(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    plugin = CJWatchdog(DummyContext())
    plugin._state["timeouts"] = {"a": 1}
    plugin._state["bans"] = {"b": True}

    await asyncio.gather(plugin._save_state(), plugin._save_state())

    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["timeouts"]["a"] == 1
    assert saved["bans"]["b"] is True


@pytest.mark.asyncio
async def test_disable_via_manager_missing(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    plugin = CJWatchdog(DummyContext())

    ok = await plugin._disable_via_manager("missing", enable=False)

    assert ok is False


@pytest.mark.asyncio
async def test_initialize_no_running_loop(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    plugin = CJWatchdog(DummyContext())

    def _raise_no_loop():
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(asyncio, "get_running_loop", _raise_no_loop)

    await plugin.initialize()

    assert plugin._init_task is None
