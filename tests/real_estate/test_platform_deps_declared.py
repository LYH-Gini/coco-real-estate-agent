"""平台适配器依赖必须由我们显式声明（2026-09-27 微信配对失败的根因）。

`aiohttp` 不在项目基础依赖里（只写在官方可选 extras），而 Coco 的安装路径只装
「基础依赖 + 房产专用包」→ 任何实例都没有 aiohttp → `coco gateway setup` 配微信时被
`check_weixin_requirements()` 拦下（现场报 "Missing dependencies: Weixin needs aiohttp
and cryptography."）。

这组用例钉住四处声明（install.sh / scripts/update.sh / 体检脚本 / CI），并统一用
「与 pyproject 里同一个钉版」的关系断言，而不是写死版本号 —— 官方升版本时这里会提示同步。
"""
import builtins
import re
import tomllib
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
UPDATE_SH = REPO_ROOT / "scripts" / "update.sh"
HEALTHCHECK = REPO_ROOT / "scripts" / "healthcheck.py"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "real-estate-tests.yml"

AIOHTTP_PIN_RE = r'"?aiohttp==([0-9][0-9.]*)"?'
CRYPTO_PIN_RE = r'"?cryptography==([0-9][0-9.]*)"?'


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _pyproject() -> dict:
    return tomllib.loads(_text(REPO_ROOT / "pyproject.toml"))


def _extras_aiohttp_pins() -> set:
    pins = set()
    for spec in (_pyproject().get("project", {}).get("optional-dependencies") or {}).values():
        for item in spec:
            match = re.match(r'aiohttp==([0-9][0-9.]*)', item)
            if match:
                pins.add(match.group(1))
    return pins


def _base_cryptography_pin():
    for item in _pyproject().get("project", {}).get("dependencies") or []:
        match = re.match(r'cryptography==([0-9][0-9.]*)', item)
        if match:
            return match.group(1)
    return None


class TestDeclaredEverywhere:
    def test_install_sh_declares_both(self):
        # 两条 pip 行（国内镜像 + 官方源兜底）都要带上，否则兜底路径装不全
        t = _text(INSTALL_SH)
        assert t.count("aiohttp==") >= 2, "install.sh 的两条 pip 行都应声明 aiohttp"
        assert t.count("cryptography==") >= 2, "install.sh 的两条 pip 行都应声明 cryptography"

    def test_update_sh_declares_both(self):
        t = _text(UPDATE_SH)
        assert "aiohttp==" in t and "cryptography==" in t, "update.sh 要给已装实例补上这两个包"

    def test_healthcheck_reports_aiohttp(self):
        assert '"aiohttp"' in _text(HEALTHCHECK), "coco check 的依赖清单要包含 aiohttp"

    def test_healthcheck_has_platform_dep_section(self):
        t = _text(HEALTHCHECK)
        assert "real_estate_platform_deps" in t, "coco check 应调用平台依赖体检模块"

    def test_ci_declares_aiohttp(self):
        assert "aiohttp==" in _text(CI_WORKFLOW), "CI 的依赖清单要与用户环境同构"

    def test_versions_match_pyproject(self):
        pins = _extras_aiohttp_pins()
        assert pins, "pyproject 的 extras 里应有 aiohttp 钉版"
        assert len(pins) == 1, f"pyproject 里 aiohttp 钉版不一致：{pins}"
        want_aiohttp = pins.pop()
        for path in (INSTALL_SH, UPDATE_SH, CI_WORKFLOW):
            found = set(re.findall(AIOHTTP_PIN_RE, _text(path)))
            assert found, f"{path.name} 未声明 aiohttp 钉版"
            assert found == {want_aiohttp}, (
                f"{path.name} 的 aiohttp 钉版 {found} 与 pyproject 的 {want_aiohttp} 不一致"
            )
        want_crypto = _base_cryptography_pin()
        assert want_crypto, "pyproject 基础依赖里应有 cryptography 钉版"
        for path in (INSTALL_SH, UPDATE_SH):
            found = set(re.findall(CRYPTO_PIN_RE, _text(path)))
            assert found == {want_crypto}, (
                f"{path.name} 的 cryptography 钉版 {found} 与 pyproject 的 {want_crypto} 不一致"
            )


class TestPlatformDepState:
    def test_configured_platforms_reads_env(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("FEISHU_APP_ID=cli_xxx\nUNRELATED=1\n", encoding="utf-8")
        from agent.real_estate_platform_deps import configured_platforms

        found = configured_platforms(str(env))
        assert found, "应在 .env 里认出已配置的平台"
        assert "feishu" in [key for key, _ in found]

    def test_empty_env_reports_nothing_configured(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# 没有平台密钥\n", encoding="utf-8")
        from agent.real_estate_platform_deps import configured_platforms

        assert configured_platforms(str(env)) == []

    def test_unreadable_table_is_distinguished_from_empty(self, tmp_path, monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("hermes_cli.gateway_setup_wizard"):
                raise ImportError("simulated missing wizard table")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        from agent.real_estate_platform_deps import configured_platforms

        assert configured_platforms(str(tmp_path / ".env")) is None, \
            "读不到官方向导表要返回 None（与“一个都没配”区分开）"

    def test_weixin_deps_present_in_this_env(self):
        pytest.importorskip("aiohttp")
        pytest.importorskip("cryptography")
        from agent.real_estate_platform_deps import platform_dep_state

        state, detail = platform_dep_state("weixin")
        assert state is True, f"本环境应通过微信适配器的依赖检查（{detail}）"

    def test_unknown_platform_is_undecided(self):
        from agent.real_estate_platform_deps import platform_dep_state

        state, detail = platform_dep_state("不存在的平台-测试")
        assert state is None, "查不了的平台不能报成缺依赖"
        assert detail

    def test_missing_dep_is_reported_as_false(self, monkeypatch):
        """检查函数返回 False 时要如实报 False —— 这正是微信那次的判定路径。"""
        fake_mod = types.ModuleType("plugins.platforms.weixin.adapter")
        fake_mod.check_weixin_requirements = lambda: False
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "plugins.platforms.weixin.adapter":
                return fake_mod
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        from agent.real_estate_platform_deps import platform_dep_state

        state, detail = platform_dep_state("weixin")
        assert state is False
        assert "weixin" in detail

    def test_registry_check_fn_fallback(self, monkeypatch):
        """适配器模块导不进来时（最常见：SDK 没装），用平台注册表登记的依赖门兜底。"""
        from gateway.platform_registry import platform_registry

        class _Entry:
            @staticmethod
            def check_fn():
                return False

        monkeypatch.setattr(platform_registry, "get", lambda name, *args, **kwargs: _Entry())
        from agent.real_estate_platform_deps import platform_dep_state

        state, detail = platform_dep_state("模块导不进来的平台-测试")
        assert state is False, "SDK 没装属于缺依赖，不能报成判断不了"
        assert "platform_registry" in detail

    def test_checker_exception_is_undecided_not_failure(self, monkeypatch):
        fake_mod = types.ModuleType("plugins.platforms.weixin.adapter")

        def boom():
            raise RuntimeError("adapter blew up")

        fake_mod.check_weixin_requirements = boom
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "plugins.platforms.weixin.adapter":
                return fake_mod
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        from agent.real_estate_platform_deps import platform_dep_state

        state, _detail = platform_dep_state("weixin")
        assert state is None, "适配器自己的检查函数抛错，只报“判断不了”，不冤枉成缺依赖"
