from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType, permission_type
from astrbot.api.star import Context, Star, StarTools, register

PLUGIN_ID = "cj_watchdog"
PLUGIN_NAME = "超时插件守护"
PLUGIN_VERSION = "1.0.0"
PLUGIN_AUTHOR = "codex"
LOG_PREFIX = f"[{PLUGIN_ID}]"
SYSTEM_CONTEXT = "context=system"

INIT_TIMEOUT_SECONDS = 20
MAX_TIMEOUTS = 3


@register(PLUGIN_ID, PLUGIN_NAME, "检测插件初始化超时并可手动ban，提供/testapi", PLUGIN_VERSION, PLUGIN_AUTHOR)
class CJWatchdog(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._settings = self._load_settings()
        self._init_timeout_seconds = self._settings["init_timeout_seconds"]
        self._max_timeouts = self._settings["max_timeouts"]
        self._data_dir = self._resolve_data_dir()
        self._state_path = self._data_dir / "state.json"
        self._state: Dict[str, Any] = self._load_state()
        self._timeouts: Dict[str, int] = self._state["timeouts"]
        self._bans: Dict[str, bool] = self._state["bans"]
        self._save_lock = asyncio.Lock()
        self._init_task: Optional[asyncio.Task] = None
        self._log_info("Initialized. data_dir=%s state_path=%s", self._data_dir, self._state_path)
        self._log_info("Loaded state: timeouts=%d bans=%d", len(self._timeouts), len(self._bans))

    async def initialize(self) -> None:
        if self._init_task and not self._init_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._log_warning("Init-timeout scan not started: no running event loop.")
            return
        self._init_task = loop.create_task(self._check_timeouts_loop())
        self._log_info("Init-timeout scan loop started. interval=%s", self._init_timeout_seconds)

    async def terminate(self) -> None:
        self._log_info("Terminating plugin.")
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
            try:
                await self._init_task
            except asyncio.CancelledError:
                pass

    def _log_info(self, message: str, *args: Any) -> None:
        logger.info("%s " + message, LOG_PREFIX, *args)

    def _log_warning(self, message: str, *args: Any, exc_info: bool = False) -> None:
        logger.warning("%s " + message, LOG_PREFIX, *args, exc_info=exc_info)

    def _event_context(self, event: AstrMessageEvent, mask: bool = True) -> str:
        parts = []
        umo = getattr(event, "unified_msg_origin", None)
        if umo is not None:
            for attr in (
                "platform",
                "group_id",
                "channel_id",
                "guild_id",
                "user_id",
                "sender_id",
                "bot_id",
                "app_id",
            ):
                value = getattr(umo, attr, None)
                if value not in (None, "", 0):
                    if mask and attr in {"user_id", "sender_id"}:
                        value = self._mask_value(value)
                    parts.append(f"{attr}={value}")
        for attr in ("platform", "group_id", "channel_id", "guild_id", "user_id", "sender_id"):
            value = getattr(event, attr, None)
            if value not in (None, "", 0):
                if mask and attr in {"user_id", "sender_id"}:
                    value = self._mask_value(value)
                parts.append(f"{attr}={value}")
        if not parts:
            parts.append("context=unknown")
        return " ".join(parts)

    def _mask_value(self, value: Any) -> str:
        text = str(value)
        if len(text) <= 4:
            return "****"
        return f"{text[:4]}****"

    def _resolve_data_dir(self) -> Path:
        data_dir = StarTools.get_data_dir(PLUGIN_ID)
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir

    def _load_settings(self) -> Dict[str, int]:
        config = self.context.get_config()
        plugin_cfg: Dict[str, Any] = {}
        if isinstance(config, dict):
            base_cfg = config.get(PLUGIN_ID, {})
            if isinstance(base_cfg, dict):
                plugin_cfg.update(base_cfg)
            plugins_cfg = config.get("plugins", {})
            if isinstance(plugins_cfg, dict):
                nested_cfg = plugins_cfg.get(PLUGIN_ID)
                if isinstance(nested_cfg, dict):
                    plugin_cfg = {**nested_cfg, **plugin_cfg}
        init_timeout = self._coerce_positive_int(
            plugin_cfg.get("init_timeout_seconds", INIT_TIMEOUT_SECONDS), INIT_TIMEOUT_SECONDS
        )
        max_timeouts = self._coerce_positive_int(
            plugin_cfg.get("max_timeouts", MAX_TIMEOUTS), MAX_TIMEOUTS
        )
        return {"init_timeout_seconds": init_timeout, "max_timeouts": max_timeouts}

    def _coerce_positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def _load_state(self) -> Dict[str, Any]:
        def _normalize_state(data: Dict[str, Any]) -> Dict[str, Any]:
            timeouts = data.get("timeouts")
            bans = data.get("bans")
            data["timeouts"] = timeouts if isinstance(timeouts, dict) else {}
            data["bans"] = bans if isinstance(bans, dict) else {}
            return data

        if not self._state_path.exists():
            self._log_info("State file not found, initializing new state: %s", self._state_path)
            return {"timeouts": {}, "bans": {}}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return _normalize_state(data)
        except Exception as exc:
            self._log_warning("Failed to load %s: %s", self._state_path, exc)
        return {"timeouts": {}, "bans": {}}

    async def _save_state(self) -> None:
        async with self._save_lock:
            try:
                temp_path = self._state_path.with_suffix(".json.tmp")
                temp_path.write_text(
                    json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                temp_path.replace(self._state_path)
            except Exception as exc:
                self._log_warning("Failed to save %s: %s", self._state_path, exc)

    async def _check_timeouts_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._init_timeout_seconds)
                await self._check_timeouts_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_warning("Init-timeout scan loop error: %s", exc, exc_info=True)

    async def _check_timeouts_once(self) -> None:
        stars = self.context.get_all_stars()
        self._log_info(
            "Scanning %d plugins for init timeout. ctx=%s", len(stars), SYSTEM_CONTEXT
        )
        known_names = set()
        for meta in stars:
            name = getattr(meta, "name", "")
            if not name or name == PLUGIN_ID:
                continue
            known_names.add(name)
            if getattr(meta, "reserved", False):
                continue
            if self._bans.get(name):
                continue
            if getattr(meta, "activated", True):
                if name in self._timeouts:
                    self._timeouts.pop(name, None)
                continue
            new_count = int(self._timeouts.get(name, 0)) + 1
            self._timeouts[name] = new_count
            self._log_warning(
                "Plugin %s init timeout count: %s ctx=%s", name, new_count, SYSTEM_CONTEXT
            )
            if new_count >= self._max_timeouts:
                await self._ban_plugin(name, reason="init timeout", ctx=SYSTEM_CONTEXT)
        for name in list(self._timeouts.keys()):
            if self._bans.get(name) or name not in known_names:
                self._timeouts.pop(name, None)
        await self._save_state()

    def _get_star_manager(self) -> Optional[Any]:
        for attr in ("star_manager", "plugin_manager", "_star_manager", "_plugin_manager"):
            manager = getattr(self.context, attr, None)
            if manager:
                return manager
        core = getattr(self.context, "core", None) or getattr(self.context, "_core", None)
        if core:
            for attr in ("star_manager", "plugin_manager", "_star_manager", "_plugin_manager"):
                manager = getattr(core, attr, None)
                if manager:
                    return manager
        return None

    async def _call_manager(self, manager: Any, method: str, *args: Any) -> bool:
        func = getattr(manager, method, None)
        if not callable(func):
            return False
        sig: Optional[inspect.Signature] = None
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):
            sig = None
        if sig is not None:
            try:
                sig.bind_partial(*args)
            except TypeError:
                return False
        try:
            result = func(*args)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, bool):
                return result
            return True
        except TypeError as exc:
            self._log_warning(
                "Manager method %s raised TypeError: %s", method, exc, exc_info=True
            )
            return False
        except Exception as exc:
            self._log_warning("Manager method %s failed: %s", method, exc, exc_info=True)
            return False

    async def _disable_via_manager(self, name: str, enable: bool) -> bool:
        manager = self._get_star_manager()
        if not manager:
            self._log_warning("No star manager found.")
            return False
        if await self._call_manager(manager, "set_star_activated", name, enable):
            return True
        if enable:
            methods = ("enable_star", "activate_star", "enable", "activate")
        else:
            methods = ("disable_star", "deactivate_star", "disable", "deactivate", "ban_star")
        for method in methods:
            if await self._call_manager(manager, method, name):
                return True
        return False

    async def _ban_plugin(self, name: str, reason: str, ctx: str = SYSTEM_CONTEXT) -> None:
        self._bans[name] = True
        if not await self._disable_via_manager(name, enable=False):
            meta = self.context.get_registered_star(name)
            if meta is not None:
                try:
                    setattr(meta, "activated", False)
                except Exception:
                    pass
        self._log_warning("Plugin %s banned (%s) ctx=%s", name, reason, ctx)
        await self._save_state()

    async def _unban_plugin(self, name: str, ctx: str = SYSTEM_CONTEXT) -> bool:
        self._log_info("Unban request: %s ctx=%s", name, ctx)
        if name in self._bans:
            del self._bans[name]
        enabled = await self._disable_via_manager(name, enable=True)
        await self._save_state()
        return enabled

    @permission_type(PermissionType.ADMIN)
    @filter.command("cjban")
    async def cjban(self, event: AstrMessageEvent, name: str = "", action: str = ""):
        self._log_info(
            "Command cjban called. name=%s action=%s ctx=%s",
            name or "-",
            action or "toggle",
            self._event_context(event),
        )
        if not name:
            yield event.plain_result("用法: /cjban 插件名 [on|off|toggle]")
            return
        if len(name) > 64:
            yield event.plain_result("插件名过长")
            return
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            yield event.plain_result("插件名格式不正确，只能包含字母、数字、下划线和连字符")
            return
        action = (action or "toggle").lower()
        ctx = self._event_context(event)
        meta = self.context.get_registered_star(name)
        if meta is None and not self._bans.get(name):
            if action not in ("off", "unban", "false", "0"):
                yield event.plain_result(f"未找到插件: {name}")
                return
        if action in ("on", "ban", "true", "1"):
            await self._ban_plugin(name, reason="manual", ctx=ctx)
            yield event.plain_result(f"已ban插件: {name}")
            return
        if action in ("off", "unban", "false", "0"):
            enabled = await self._unban_plugin(name, ctx=ctx)
            if enabled:
                yield event.plain_result(f"已解除ban并尝试启用插件: {name}")
            else:
                yield event.plain_result(f"已解除ban，但未找到启用接口: {name}")
            return
        if self._bans.get(name):
            enabled = await self._unban_plugin(name, ctx=ctx)
            if enabled:
                yield event.plain_result(f"已解除ban并尝试启用插件: {name}")
            else:
                yield event.plain_result(f"已解除ban，但未找到启用接口: {name}")
        else:
            await self._ban_plugin(name, reason="manual", ctx=ctx)
            yield event.plain_result(f"已ban插件: {name}")

    @filter.command("testapi")
    async def testapi(self, event: AstrMessageEvent):
        self._log_info("Command testapi called. ctx=%s", self._event_context(event))
        provider = self.context.get_using_provider(umo=event.unified_msg_origin)
        if not provider:
            yield event.plain_result("当前未启用模型。")
            return
        provider_id = getattr(provider, "id", None)
        provider_type = getattr(provider, "type", None)
        model = getattr(provider, "model", None)
        config = getattr(provider, "provider_config", None) or getattr(provider, "config", None)
        if isinstance(config, dict):
            provider_id = provider_id or config.get("id") or config.get("name")
            provider_type = provider_type or config.get("type")
            model_cfg = config.get("model_config") or {}
            if isinstance(model_cfg, dict):
                model = model or model_cfg.get("model") or model_cfg.get("model_name")
        parts = []
        if provider_id:
            parts.append(f"id={provider_id}")
        if provider_type:
            parts.append(f"type={provider_type}")
        if model:
            parts.append(f"model={model}")
        if not parts:
            parts.append(f"class={provider.__class__.__name__}")
        yield event.plain_result("当前模型信息: " + ", ".join(parts))
