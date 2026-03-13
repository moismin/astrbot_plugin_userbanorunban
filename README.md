# CJ Watchdog (超时插件守护)

检测插件初始化超时并支持手动 ban/unban，同时提供 `/testapi` 便于查看当前模型信息。

## 功能
- 监控插件初始化超时：启动后等待 `INIT_TIMEOUT_SECONDS` 秒，扫描未激活插件并累计超时次数。
- 自动 ban：同一插件超时次数达到 `MAX_TIMEOUTS` 时自动禁用。
- 手动 ban/unban：通过命令控制插件启用状态。
- 查看当前模型信息：通过命令输出当前使用的模型与提供方。
- 日志输出：所有关键路径均输出日志，包含上下文（群号/来源等）。

## 命令
- `/cjban 插件名 [on|off|toggle]`
  - `on/ban/true/1`：手动 ban
  - `off/unban/false/0`：解除 ban 并尝试启用
  - 省略 `action` 时：自动 toggle
- `/testapi`
  - 输出当前模型提供方与模型信息（若已启用）

## 配置
在 `main.py` 中可调整：
- `INIT_TIMEOUT_SECONDS`：初始化等待时间（秒）
- `MAX_TIMEOUTS`：超时累计阈值

## 数据与状态
插件状态保存在 `data/cj_watchdog/state.json`（路径会根据运行环境自动选择可写目录）。
内容包括：
- `timeouts`：插件超时计数
- `bans`：插件 ban 状态

## 日志
日志前缀为 `[cj_watchdog]`，并尽可能附带上下文：
- 命令入口：包含群号/频道/平台/用户等（基于 `event.unified_msg_origin`）
- 系统任务：标记为 `context=system`

