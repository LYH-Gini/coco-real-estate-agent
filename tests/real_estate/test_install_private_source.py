"""私有源安装（COCO_GIT_URL）的回归测试。

背景：开发版分支搬到不对外的私有仓库后，正式版的安装命令保持原样不变；开发版靠
COCO_GIT_URL 指定地址安装，凭据（SSH 部署公钥或访问令牌）由 git 自己读取，
脚本本身不碰任何凭据。这里做静态守护：这些行为被改掉时立刻报红。
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL = REPO_ROOT / "install.sh"


def _text() -> str:
    return INSTALL.read_text(encoding="utf-8")


class TestInstallPrivateSource:
    def test_env_override_with_safe_default(self):
        t = _text()
        assert 'COCO_GIT_URL="${COCO_GIT_URL:-}"' in t, \
            "应支持 COCO_GIT_URL 环境变量且默认留空，这样不指定时正式版安装路径完全不变"

    def test_private_branch_runs_before_source_probe(self):
        t = _text()
        body = t[t.index("clone_project() {"):]
        assert body.index("$COCO_GIT_URL") < body.index("$(probe_source)"), \
            "私有源分支必须排在双源探测之前，否则指定了私有源还会去探测公开源"

    def test_private_clone_does_not_hang_on_password_prompt(self):
        t = _text()
        assert 'GIT_TERMINAL_PROMPT=0 git clone --branch "$COCO_CHANNEL" "$COCO_GIT_URL"' in t, \
            "私有仓没配好凭据时要立刻失败并说明原因，不能卡在等待输入密码"

    def test_failure_hint_explains_what_to_configure(self):
        t = _text()
        assert "凭据" in t and "部署公钥" in t, "失败提示里要写清需要配什么凭据"

    def test_no_private_repo_hardcoded(self):
        t = _text()
        assert "coco-real-estate-agent-dev" not in t, \
            "私有仓库地址不能写死进脚本，应由 COCO_GIT_URL 传入"

    def test_git_output_is_masked_before_printing(self):
        t = _text()
        assert re.search(r"sed -E 's#//\[\^@/\]\*@#//\*\*\*@#g'", t), \
            "把 git 的输出打出来之前要遮掉地址里的用户名/令牌"

    def test_usage_comment_documents_private_source(self):
        t = _text()
        assert "私有源安装" in t and "COCO_GIT_URL" in t, "用法注释里要写清私有源怎么装"

    def test_public_install_path_unchanged(self):
        t = _text()
        assert 'GITEE_RAW_URL="https://gitee.com/LYH-Gini/coco-real-estate-agent/raw/master/install.sh"' in t
        assert 'GITEE_REPO_URL="https://gitee.com/LYH-Gini/coco-real-estate-agent.git"' in t
        assert 'GITHUB_REPO_URL="https://github.com/LYH-Gini/coco-real-estate-agent.git"' in t
