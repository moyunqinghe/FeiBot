"""MCP 插件生命周期管理:把一个 MCP server 当作一个插件装入/卸下 agent。

装 = discover() 取清单 → 加前缀注册进 TOOL_REGISTRY → 落库;
卸 = 按归属把工具移出注册表 → 删库。归属映射在本模块维护,卸载不依赖
server 在线。第一步 handler 为占位(远程执行 tools/call 是第二步)。
"""

from __future__ import annotations

import json
import logging
import re

from mcp_discovery import McpDiscoveryError, McpServerConfig, discover

from app.agent.tools.registry import ToolSpec, register_tool, unregister_tool
from app.db import store

logger = logging.getLogger(__name__)

_SEPARATOR = "__"
_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class PluginError(Exception):
    """插件管理层错误(非法名、插件不存在等)。"""


class McpPluginManager:
    def __init__(self) -> None:
        self._provenance: dict[str, set[str]] = {}  # 插件名 -> 已注册工具 key
        self._status: dict[str, str] = {}           # 插件名 -> 最近加载状态

    # ---- 内部 ----
    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or not _PLUGIN_NAME_RE.match(name):
            raise PluginError(f"非法插件名:{name!r}(仅允许字母/数字/下划线/连字符)")
        if _SEPARATOR in name:
            raise PluginError(f"插件名不能包含 {_SEPARATOR!r}:{name!r}")

    @staticmethod
    def _normalize_config(config) -> dict:
        if isinstance(config, McpServerConfig):
            cfg = config.model_dump()
        else:
            cfg = dict(config)
        # 先把 type 别名为 transport,再走 McpServerConfig 做值归一化
        # (如 streamable_http -> http)。
        if "type" in cfg and "transport" not in cfg:
            cfg["transport"] = cfg.pop("type")
        normalized = McpServerConfig(**cfg).model_dump(exclude_none=True)
        return normalized

    @staticmethod
    def _placeholder_handler(plugin: str, tool: str):
        def handler(**args) -> str:
            return f"工具 {plugin}{_SEPARATOR}{tool} 来自 MCP 插件 {plugin},远程执行尚未接入。"
        return handler

    def _register_tools(self, name: str, tools) -> set[str]:
        keys: set[str] = set()
        for t in tools:
            key = f"{name}{_SEPARATOR}{t.name}"
            params = {
                pname: str(pdef.get("description") or pdef.get("type") or "")
                for pname, pdef in (t.input_schema.get("properties") or {}).items()
                if isinstance(pdef, dict)
            }
            register_tool(ToolSpec(
                name=key,
                description=t.description or f"MCP 工具 {t.name}",
                parameters=params,
                handler=self._placeholder_handler(name, t.name),
            ))
            keys.add(key)
        return keys

    def _remove_tools(self, name: str) -> int:
        removed = 0
        for key in list(self._provenance.get(name, ())):
            if unregister_tool(key):
                removed += 1
        self._provenance.pop(name, None)
        return removed

    # ---- 公共 API ----
    def install(self, name: str, config) -> int:
        """发现并装入插件;同名幂等重装。返回注册的工具数。"""
        self._validate_name(name)
        cfg = self._normalize_config(config)
        result = discover(cfg)  # 失败抛 McpDiscoveryError,不注册不落库
        self._remove_tools(name)
        keys = self._register_tools(name, result.tools)
        self._provenance[name] = keys
        self._status[name] = f"ok, {len(keys)} tools"
        store.upsert_plugin(name, json.dumps(cfg, ensure_ascii=False), 1)
        return len(keys)

    def uninstall(self, name: str) -> bool:
        """卸下插件:移出其工具并删除库记录。不联网;不存在返回 False。"""
        existed_db = store.delete_plugin(name)
        removed = self._remove_tools(name)
        self._status.pop(name, None)
        return existed_db or removed > 0

    def disable(self, name: str) -> bool:
        """停用:移出工具但保留配置。不存在返回 False。"""
        if store.get_plugin(name) is None:
            return False
        self._remove_tools(name)
        store.set_plugin_enabled(name, 0)
        self._status[name] = "disabled"
        return True

    def enable(self, name: str) -> int:
        """启用:重新 discover 并注册。不存在抛 PluginError。"""
        row = store.get_plugin(name)
        if row is None:
            raise PluginError(f"插件不存在:{name}")
        try:
            result = discover(json.loads(row["config_json"]))
        except Exception as exc:  # noqa: BLE001 — 记录失败状态再抛给调用方
            self._status[name] = f"enable failed: {exc}"
            raise
        self._remove_tools(name)
        keys = self._register_tools(name, result.tools)
        self._provenance[name] = keys
        store.set_plugin_enabled(name, 1)
        self._status[name] = f"ok, {len(keys)} tools"
        return len(keys)

    def reload(self, name: str) -> int:
        """重新 discover 并 diff 更新已注册工具。不存在或已停用抛 PluginError。"""
        row = store.get_plugin(name)
        if row is None:
            raise PluginError(f"插件不存在:{name}")
        if not row["enabled"]:
            raise PluginError(f"插件 {name} 已停用,请先 enable 再 reload")
        result = discover(json.loads(row["config_json"]))
        self._remove_tools(name)
        keys = self._register_tools(name, result.tools)
        self._provenance[name] = keys
        store.upsert_plugin(name, row["config_json"], row["enabled"])
        self._status[name] = f"ok, {len(keys)} tools"
        return len(keys)

    def list(self) -> list[dict]:
        """列出全部插件及状态(启用、已注册工具、最近加载结果)。"""
        out = []
        for row in store.list_plugins():
            name = row["name"]
            out.append({
                "name": name,
                "enabled": bool(row["enabled"]),
                "registered": sorted(self._provenance.get(name, ())),
                "status": self._status.get(name, ""),
            })
        return out

    def load_enabled(self) -> None:
        """启动钩子:重载所有启用插件;单个失败只记录,不拖累其他与启动。"""
        for row in store.list_plugins():
            if not row["enabled"]:
                continue
            name = row["name"]
            try:
                result = discover(json.loads(row["config_json"]))
                self._remove_tools(name)
                keys = self._register_tools(name, result.tools)
                self._provenance[name] = keys
                self._status[name] = f"ok, {len(keys)} tools"
            except Exception as exc:  # noqa: BLE001 — 单个插件失败不影响整体
                logger.warning("启动加载 MCP 插件 %s 失败:%s", name, exc)
                self._status[name] = f"load failed: {exc}"

    def install_from_mcp_servers(self, payload: dict) -> dict:
        """吃标准 {"mcpServers": {name: {...}}} 信封批量装;逐项记录成败。"""
        servers = payload.get("mcpServers") or {}
        results: dict[str, object] = {}
        for name, cfg in servers.items():
            try:
                results[name] = self.install(name, cfg)
            except Exception as exc:  # noqa: BLE001
                results[name] = f"failed: {exc}"
        return results


# 模块级默认实例,供 main.py / 编程式调用
plugin_manager = McpPluginManager()


def load_enabled_plugins() -> None:
    plugin_manager.load_enabled()
