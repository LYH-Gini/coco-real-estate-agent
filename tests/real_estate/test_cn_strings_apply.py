"""中文文案重打脚本（scripts/coco_cn_strings.py）的行为守卫

为什么要它：同步官方底座是「快照式替换」，我们改过的官方文件会被换回英文。
这个脚本把「文件 + 官方原文 + 中文文案」登记成表，同步后 `--apply` 自动改回来。
本文件钉住两件事：① 表里每一条中文现在都在位；② 三种判定（OK / APPLY / ANCHOR）不会被写错。
"""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("coco_cn_strings", REPO / "scripts" / "coco_cn_strings.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["coco_cn_strings"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_every_entry_is_currently_in_place():
    """表里每一条中文都必须已经在文件里 —— 被换回英文/锚点失效都要在这里报出来"""
    mod = _load()
    problems = []
    for entry in mod.ENTRIES:
        status, detail = mod.classify(REPO, entry)
        if status != "OK":
            problems.append(f"{status} {entry[0]}: {detail or '文案被换回英文了'}")
    assert not problems, "中文文案未就位：\n" + "\n".join(problems)


def test_table_entries_are_well_formed():
    mod = _load()
    assert len(mod.ENTRIES) >= 30, "重打表条目太少，像是被截断了"
    for rel, old, new in mod.ENTRIES:
        assert (REPO / rel).exists(), f"表里的文件不存在：{rel}"
        assert old.strip() and new.strip(), f"空条目：{rel}"
        assert old != new, f"官方原文与中文相同（白替换）：{rel}"


def test_classify_distinguishes_the_three_states(tmp_path):
    mod = _load()
    entry = ("probe.txt", "官方原文", "中文文案")

    (tmp_path / "probe.txt").write_text("中文文案在那里", encoding="utf-8")
    assert mod.classify(tmp_path, entry)[0] == "OK"

    (tmp_path / "probe.txt").write_text("官方原文还在", encoding="utf-8")
    assert mod.classify(tmp_path, entry)[0] == "APPLY"

    (tmp_path / "probe.txt").write_text("官方把这段整个改了", encoding="utf-8")
    status, detail = mod.classify(tmp_path, entry)
    assert status == "ANCHOR" and detail, "锚点失效必须报出来，不能静默通过"

    (tmp_path / "probe.txt").unlink()
    assert mod.classify(tmp_path, entry)[0] == "ANCHOR"
