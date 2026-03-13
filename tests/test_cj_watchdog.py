import asyncio
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


class DummyContext:
    def __init__(self, config=None, stars=None, manager=None) -> None:
        self._config = config or {}
        self._stars = stars or []
        self._star_manager = manager

    def get_config(self, umo=None):
        return self._config

    def get_all_stars(self):
        return self._stars

    def get_registered_star(self, name):
        for star in self._stars:
            if getattr(star, "name", None) == name:
                return star
        return None


def _find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "astrbot").is_dir():
            return candidate
    raise RuntimeError("Repository root not found")


@pytest.fixture
def plugin_module():
    plugin_root = Path(__file__).resolve().parents[1]
    repo_root = _find_repo_root(plugin_root)
    inserted = False
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
        inserted = True

    from astrbot.core.star.star import star_map, star_registry

    registry_before = list(star_registry)
    map_before = dict(star_map)
    module_name = f"cj_watchdog_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, plugin_root / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    try:
        yield module
    finally:
        sys.modules.pop(module_name, None)
        star_registry[:] = registry_before
        star_map.clear()
        star_map.update(map_before)
        if inserted and sys.path and sys.path[0] == str(repo_root):
            sys.path.pop(0)
        elif inserted and str(repo_root) in sys.path:
            sys.path.remove(str(repo_root))


@pytest.fixture
def plugin_api(plugin_module, monkeypatch, tmp_path):
    async def _default_global_get(key, default=None):
        return default

    monkeypatch.setattr(plugin_module.StarTools, "get_data_dir", lambda plugin_name=None: tmp_path)
    monkeypatch.setattr(plugin_module.sp, "global_get", _default_global_get)
    return plugin_module.CJWatchdog, plugin_module


def test_dirty_state_file_sanitized(tmp_path, plugin_api):
    watchdog_cls, _ = plugin_api
    dirty_state = {
        "timeouts": {"bad": "oops", 1: -5, "ok": "2"},
        "bans": {"a": "yes", "b": "no", "c": 1, "d": 0, "e": None},
    }
    (tmp_path / "state.json").write_text(json.dumps(dirty_state), encoding="utf-8")

    plugin = watchdog_cls(DummyContext())

    assert plugin._timeouts["bad"] == 0
    assert plugin._timeouts["1"] == 0
    assert plugin._timeouts["ok"] == 2
    assert plugin._bans["a"] is True
    assert plugin._bans["b"] is False
    assert plugin._bans["c"] is True
    assert plugin._bans["d"] is False
    assert plugin._bans["e"] is False


@pytest.mark.asyncio
async def test_concurrent_save_state(tmp_path, plugin_api):
    watchdog_cls, _ = plugin_api
    plugin = watchdog_cls(DummyContext())
    plugin._state["timeouts"] = {"a": 1}
    plugin._state["bans"] = {"b": True}

    await asyncio.gather(plugin._save_state(), plugin._save_state())

    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["timeouts"]["a"] == 1
    assert saved["bans"]["b"] is True


@pytest.mark.asyncio
async def test_disable_via_manager_missing(plugin_api):
    watchdog_cls, _ = plugin_api
    plugin = watchdog_cls(DummyContext())

    ok = await plugin._disable_via_manager("missing", enable=False)

    assert ok is False


@pytest.mark.asyncio
async def test_initialize_no_running_loop(plugin_api, monkeypatch):
    watchdog_cls, _ = plugin_api
    plugin = watchdog_cls(DummyContext())

    def _raise_no_loop():
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(asyncio, "get_running_loop", _raise_no_loop)

    await plugin.initialize()

    assert plugin._init_task is None


@pytest.mark.asyncio
async def test_check_timeouts_skips_manually_disabled_plugin(plugin_api, monkeypatch):
    watchdog_cls, plugin_module = plugin_api
    star = SimpleNamespace(
        name="manual_off",
        reserved=False,
        activated=False,
        module_path="data.plugins.manual_off.main",
    )

    async def _global_get(key, default=None):
        if key == "inactivated_plugins":
            return [star.module_path]
        return default

    monkeypatch.setattr(plugin_module.sp, "global_get", _global_get)
    plugin = watchdog_cls(DummyContext(stars=[star]))

    await plugin._check_timeouts_once()

    assert plugin._timeouts == {}
    assert plugin._bans == {}


@pytest.mark.asyncio
async def test_unban_clears_timeout_counter(plugin_api):
    watchdog_cls, _ = plugin_api
    plugin = watchdog_cls(DummyContext())
    plugin._bans["foo"] = True
    plugin._timeouts["foo"] = 2

    enabled = await plugin._unban_plugin("foo")

    assert enabled is False
    assert "foo" not in plugin._bans
    assert "foo" not in plugin._timeouts


@pytest.mark.asyncio
async def test_disable_via_manager_accepts_none_for_known_methods(plugin_api):
    watchdog_cls, _ = plugin_api

    class Manager:
        def __init__(self) -> None:
            self.called = []

        async def turn_off_plugin(self, plugin_name):
            self.called.append(plugin_name)
            return None

    manager = Manager()
    plugin = watchdog_cls(DummyContext(manager=manager))

    ok = await plugin._disable_via_manager("demo", enable=False)

    assert ok is True
    assert manager.called == ["demo"]
