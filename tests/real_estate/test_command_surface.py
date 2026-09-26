"""命令口径一致性：提示词 / 技能文件只准给 coco 口径，且写到的 coco 命令必须在 coco.sh 里有实现。

回归对象：
  ① 经纪人问“要敲什么命令”时，Coco 曾把有现成命令的场景（备份/恢复/换模型/改配置/配对/体检）
     答成“我不清楚、找技术团队”——根因是提示词里没有命令面清单；
  ② 反向风险：底座知识里有 hermes 命令，而本机已不暴露 hermes 入口，照抄就会发敲不到的命令。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT = REPO_ROOT / "agent" / "real_estate_prompt.py"
SKILL = REPO_ROOT / "skills" / "real_estate" / "SKILL.md"
COCO_SH = REPO_ROOT / "scripts" / "coco.sh"

# 允许出现的底座命令（只作为“别用这些”的禁止清单出现）
ALLOWED_HERMES_SUBCOMMANDS = ("update", "gateway", "uninstall")
# 一旦出现就说明在推荐底座命令（本机敲不到）
FORBIDDEN_HERMES = r"hermes (model|setup|backup|restore|pairing|tools|doctor|uninstall)\b"

# 命令面必须覆盖的场景（问法 → 命令）
REQUIRED_MAPPINGS = (
    "coco model",
    "coco check",
    "coco backup",
    "coco restore --file",
    "coco pairing approve feishu",
    "coco config set",
    "coco tools",
)


def _supported_subcommands() -> set:
    """从 coco.sh 的 case 标签里解析真正实现的子命令。"""
    text = COCO_SH.read_text(encoding="utf-8")
    block = text.split('case "${1:-version}" in', 1)[1].split("\nesac", 1)[0]
    subs = set()
    for line in block.splitlines():
        m = re.match(r"^\s{2}([a-z|'\"_-]+)\)", line)
        if not m:
            continue
        for tok in m.group(1).split("|"):
            tok = tok.strip().strip("\"'")
            if tok and all(c.isalnum() or c in "-_" for c in tok):
                subs.add(tok)
    return subs


class TestPromptCommandSurface:
    def test_general_rule_present(self):
        t = PROMPT.read_text(encoding="utf-8")
        assert "【对外命令口径】" in t, "提示词缺少【对外命令口径】段"
        assert "一律给 `coco <子命令>`" in t, "提示词缺少“只给 coco 口径”的通用规则"
        assert "不许编命令" in t, "提示词缺少“没有对应命令时不许编”的兜底规则"
        assert "只读诊断类可以你自己跑" in t, "提示词缺少“只读诊断类可以自己跑”的口径"
        assert "改状态类绝不自己执行" in t, "提示词缺少“改状态类禁止自跑”的口径"

    def test_required_mappings_present(self):
        t = PROMPT.read_text(encoding="utf-8")
        missing = [m for m in REQUIRED_MAPPINGS if m not in t]
        assert not missing, f"提示词缺少命令映射：{missing}"

    def test_no_forbidden_hermes_commands(self):
        t = PROMPT.read_text(encoding="utf-8")
        hits = re.findall(FORBIDDEN_HERMES, t)
        assert not hits, f"提示词出现底座命令口径（会被照抄给经纪人）：{hits}"

    def test_every_coco_command_is_implemented(self):
        supported = _supported_subcommands()
        t = PROMPT.read_text(encoding="utf-8")
        used = set(re.findall(r"coco ([a-z][a-z-]*)", t))
        unknown = sorted(u for u in used if u not in supported)
        assert not unknown, f"提示词写到但 coco.sh 没实现的子命令：{unknown}"


class TestSkillCommandSurface:
    def test_rule_present(self):
        t = SKILL.read_text(encoding="utf-8")
        assert "对外命令口径" in t, "技能文件缺少对外命令口径段"
        missing = [m for m in ("coco model", "coco restore --file", "coco pairing approve feishu")
                   if m not in t]
        assert not missing, f"技能文件缺少命令：{missing}"

    def test_no_forbidden_hermes_commands(self):
        t = SKILL.read_text(encoding="utf-8")
        hits = re.findall(FORBIDDEN_HERMES, t)
        assert not hits, f"技能文件出现底座命令口径：{hits}"

    def test_every_coco_command_is_implemented(self):
        supported = _supported_subcommands()
        t = SKILL.read_text(encoding="utf-8")
        used = set(re.findall(r"coco ([a-z][a-z-]*)", t))
        unknown = sorted(u for u in used if u not in supported)
        assert not unknown, f"技能文件写到但 coco.sh 没实现的子命令：{unknown}"


def test_parser_sanity():
    """解析器本身要能拿到真命令（否则上面的断言会变成空转）。"""
    subs = _supported_subcommands()
    for expected in ("check", "backup", "restore", "update", "status", "logs", "restart",
                     "model", "setup", "config", "doctor", "tools", "gateway", "pairing",
                     "uninstall", "cli", "help", "version"):
        assert expected in subs, f"coco.sh 解析结果缺少 {expected}（解析器可能失效）"
