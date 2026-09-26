#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""非交互式配置迁移 —— 供 scripts/update.sh 调用

为什么需要它
    官方 Hermes 的配置带版本号（config.yaml 里的 _config_version）。把底座同步到
    官方新版后，旧实例的配置版本会落后（实测：我们 33 → 官方 0.21.3 是 44），
    需要迁移补齐新配置项。
    官方命令 `hermes config migrate` 是**交互式**的（内部写死
    migrate_config(interactive=True)），在无人值守的更新脚本里会卡住等输入，
    所以这里直接调用 Python API 的非交互版本。

安全性
    · 只做官方自带的迁移：补齐缺失配置项 + 提升版本号，不删除用户已有配置
    · 任何异常都不阻断更新流程（打印提示后正常退出 0）
"""
import sys


def main() -> int:
    try:
        from hermes_cli.config import check_config_version, migrate_config
    except Exception as exc:  # 官方改了模块路径也不该阻断更新
        print(f"  配置迁移跳过：无法导入官方模块（{exc}）")
        return 0

    try:
        current, latest = check_config_version()
    except Exception as exc:
        print(f"  配置迁移跳过：读取配置版本失败（{exc}）")
        return 0

    print(f"  配置版本：{current} → {latest}")
    if current >= latest:
        print("  已是最新，无需迁移")
        return 0

    try:
        results = migrate_config(interactive=False, quiet=True)
        added = results.get("config_added") or []
        env_added = results.get("env_added") or []
        print(f"  已补齐 {len(added)} 个配置项、{len(env_added)} 个环境变量")
        for warning in results.get("warnings") or []:
            print(f"  提示：{warning}")
    except Exception as exc:
        # 迁移失败是"警告"级：配置缺项不影响启动（官方会按默认值兜底）
        print(f"  配置迁移失败（不影响更新继续，可事后手动处理）：{exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
