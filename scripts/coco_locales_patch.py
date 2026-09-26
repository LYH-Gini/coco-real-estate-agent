#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Coco 自有文案 —— 写进官方语言包 locales/*.yaml（幂等，可重复运行）

用途
    官方把「未设主页频道」这条提示的文案写死在 gateway/run_turn.py 里（英文、
    品牌名是 Hermes）。Coco 改成走 i18n 取键 coco.home_channel_missing，文案放在
    语言包里。语言包是官方文件，同步上游会被整批替换 —— 所以本脚本负责「重新追加」：
    同步完成后跑一次即可恢复。

为什么 17 种语言都要加
    tests/agent/test_i18n.py 强制「非英文语言包的键集必须与 en.yaml 完全相同」，
    只加 en/zh 会让其它 15 种语言缺键、测试直接失败。非中文语言统一用英文文案
    （与改动前的观感一致），中文/繁体用中文。

用法
    python3 scripts/coco_locales_patch.py            # 追加/修复
    python3 scripts/coco_locales_patch.py --check    # 只检查（同步后自检用）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"
KEY_BLOCK_TAG = "# Coco 自有文案"

EN = (
    "📬 No home channel is set for {platform}. A home channel is where Coco delivers "
    "cron job results and cross-platform messages.\\n\\nType {sethome_cmd} to make this "
    "chat your home channel, or ignore to skip."
)
ZH = (
    "📬 还没有给 {platform} 设置主页频道。主页频道是 Coco 定时任务结果与跨平台消息的送达通道。"
    "\\n\\n输入 {sethome_cmd} 可以把当前会话设为主页频道，或忽略本条消息跳过设置。"
)
ZH_HANT = (
    "📬 還沒有給 {platform} 設定主頁頻道。主頁頻道是 Coco 定時任務結果與跨平台訊息的送達通道。"
    "\\n\\n輸入 {sethome_cmd} 可以把當前會話設為主頁頻道，或忽略本條訊息跳過設定。"
)

TEXTS = {"zh": ZH, "zh-hant": ZH_HANT}


def block_for(lang: str) -> str:
    text = TEXTS.get(lang, EN)
    return (
        f"\n{KEY_BLOCK_TAG}（上游同步后由 scripts/coco_locales_patch.py 重新追加）\n"
        f"coco:\n"
        f"  home_channel_missing: \"{text}\"\n"
    )


def patch_file(path: Path) -> str:
    """返回 'added' | 'ok' | 'replaced'"""
    text = path.read_text(encoding="utf-8")
    lang = path.stem
    body = text.rstrip("\n") + "\n"
    want = block_for(lang)

    if KEY_BLOCK_TAG in body:
        head, _, _old = body.partition(KEY_BLOCK_TAG)
        rebuilt = head.rstrip("\n") + "\n" + want
        if rebuilt == body:
            return "ok"
        path.write_text(rebuilt, encoding="utf-8")
        return "replaced"

    path.write_text(body + want, encoding="utf-8")
    return "added"


def main(argv: list) -> int:
    check_only = "--check" in argv
    files = sorted(LOCALES.glob("*.yaml"))
    if not files:
        print(f"找不到语言包：{LOCALES}")
        return 1

    counts = {"added": 0, "ok": 0, "replaced": 0}
    bad = []
    for path in files:
        before = path.read_text(encoding="utf-8")
        if check_only:
            state = "ok" if KEY_BLOCK_TAG in before else "added"
        else:
            state = patch_file(path)
        counts[state] += 1
        if state == "added":
            bad.append(path.name)

    print(f"语言包 {len(files)} 个：补齐 {counts['added']}，已有 {counts['ok']}，重写 {counts['replaced']}")
    if bad:
        if check_only:
            print(f"缺失 coco.home_channel_missing 的语言包：{', '.join(bad)}")
            print("处理：跑 python3 scripts/coco_locales_patch.py 补齐")
            return 1
        print(f"已补齐：{', '.join(bad)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
