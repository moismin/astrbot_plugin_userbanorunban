# CJ Watchdog (超时插件守护)

检测插件初始化超时并支持手动 ban/unban，同时提供 `/testapi` 用于查看当前模型信息（管理员）。

## 功能
- 监控插件初始化超时：启动后等待 `init_timeout_seconds` 秒，扫描未激活插件并累计超时次数。
- 自动 ban：同一插件超时次数达到 `max_timeouts` 时自动禁用。
- 手动 ban/unban：通过命令控制插件启用状态（管理员）。
- 查看当前模型信息：通过命令输出当前使用的模型与提供方（管理员）。
- 日志输出：包含关键路径与上下文信息（用户 ID 脱敏）。

## 命令
- `/cjban 插件名 [on|off|toggle]`
  - `on/ban/true/1`：手动 ban
  - `off/unban/false/0`：解除 ban 并尝试启用
  - 省略 `action`：自动 toggle
- `/testapi`
  - 输出当前模型提供方与模型信息（管理员）

## 配置
可通过 AstrBot 配置文件中的 `cj_watchdog` 或 `plugins.cj_watchdog` 配置项修改默认值，**`plugins.cj_watchdog` 会覆盖顶层 `cj_watchdog`**，推荐使用更具体的配置以避免歧义。

```json
{
  "cj_watchdog": {
    "init_timeout_seconds": 20,
    "max_timeouts": 3
  },
  "plugins": {
    "cj_watchdog": {
      "init_timeout_seconds": 30,
      "max_timeouts": 5
    }
  }
}
```

- `init_timeout_seconds`：初始化等待时间（秒）
- `max_timeouts`：超时累计阈值

## 数据与状态
插件状态保存在 `data/plugin_data/cj_watchdog/state.json`（路径由 `StarTools.get_data_dir()` 管理）。内容包括：
- `timeouts`：插件超时计数
- `bans`：插件 ban 状态

## 日志
日志前缀为 `[cj_watchdog]`，并尽可能附带上下文信息：
- 命令入口：包含群号/频道/平台/用户等（基于 `event.unified_msg_origin`，用户 ID 已脱敏）
- 系统任务：标记为 `context=system`

## 测试
在仓库根目录运行以下命令：

```bash
pytest data/plugins/astrbot_plugin_userbanorunban/tests -q
```
