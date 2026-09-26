#!/usr/bin/env bash
# Coco 海报字体 + 渲染器安装（幂等、可重复执行、失败不阻塞）
#
# 装什么：
#   1) librsvg2-bin —— 海报 SVG 渲染器（约 7MB，apt）
#   2) 中文商用字体 —— 海报标题/数字/正文用（约 140MB，多源自动回退）
#
# 为什么：海报要"专业感"，靠的是字体字重层级；服务器默认只有文泉驿一种细笔画字体。
# 所有字体均为免费可商用（思源/得意黑/霞鹜文楷为 SIL OFL 开源；普惠体/MiSans/鸿蒙/站酷/庞门正道/优设
# 为各自官方声明的免费商用）。许可与来源见 docs/FONTS.md。
#
# 下载源优先级：
#   ① Gitee 字体包（一个文件，国内快；发布在 coco-real-estate-agent 的 fonts-v1 Release 附件）
#   ② 逐个字体下载（GitHub / jsDelivr 回退；外网可达的服务器用这条）
#
# 用法：
#   bash scripts/install_fonts.sh            # 安装/补齐（逐条显示进度）
#   bash scripts/install_fonts.sh --quiet    # 已就绪时少说话（update.sh 用；下载时仍显示进度）
#   COCO_FONTS_EXTRA=1 bash scripts/install_fonts.sh   # 额外装鸿蒙黑体等（下载量大）
#   COCO_SKIP_FONTS=1 ...                    # 明确跳过（海报自动回落系统字体，仍可出图）
#
# 注意：共约 140MB，视网速需 2~10 分钟。下载期间会逐条打印进度，别以为卡住了；
#      中途 Ctrl+C 也安全（已下好的会保留，重跑会跳过），不影响 COCO 使用。
set -uo pipefail

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1
FONT_DIR="/usr/local/share/fonts/coco"
# Gitee 字体包（国内首选；一个文件装齐全部字体）
BUNDLE_BASE="https://gitee.com/lyh-gini/coco-real-estate-agent/releases/download/fonts-v1"
BUNDLE_CORE="coco_fonts_core_v1.tar.gz"
BUNDLE_EXTRA="coco_fonts_extra_v1.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say()  { echo "$@"; }                                   # 进度：任何模式都显示
info() { [[ $QUIET -eq 1 ]] || echo "$@"; }             # 闲聊：quiet 模式不显示
ok()   { echo "  [OK] $*"; }
warn() { echo "  [提示] $*"; }

[[ "${COCO_SKIP_FONTS:-0}" == "1" ]] && { say "已跳过海报字体安装（COCO_SKIP_FONTS=1），海报将使用系统字体"; exit 0; }

# 字体清单：相对文件名|来源URL（多个 URL 用空格分隔，依次尝试）
CORE_FONTS=(
  "NotoSansCJKsc-Regular.otf|https://fastly.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf ghapi:https://api.github.com/repos/notofonts/noto-cjk/contents/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"
  "NotoSansCJKsc-Bold.otf|https://fastly.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf ghapi:https://api.github.com/repos/notofonts/noto-cjk/contents/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf"
  "NotoSansCJKsc-Black.otf|https://fastly.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Black.otf https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Black.otf ghapi:https://api.github.com/repos/notofonts/noto-cjk/contents/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Black.otf"
  "NotoSerifCJKsc-Black.otf|https://github.com/notofonts/noto-cjk/raw/main/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Black.otf https://fastly.jsdelivr.net/gh/notofonts/noto-cjk@main/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Black.otf ghapi:https://api.github.com/repos/notofonts/noto-cjk/contents/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Black.otf"
  "Alibaba-PuHuiTi-Heavy.ttf|https://raw.githubusercontent.com/wordshub/free-font/master/assets/font/%E4%B8%AD%E6%96%87/%E9%98%BF%E9%87%8C%E5%B7%B4%E5%B7%B4%E6%99%AE%E6%83%A0%E4%BD%93/Alibaba-PuHuiTi-Heavy.ttf https://fastly.jsdelivr.net/gh/wordshub/free-font@master/assets/font/%E4%B8%AD%E6%96%87/%E9%98%BF%E9%87%8C%E5%B7%B4%E5%B7%B4%E6%99%AE%E6%83%A0%E4%BD%93/Alibaba-PuHuiTi-Heavy.ttf ghapi:https://api.github.com/repos/wordshub/free-font/contents/assets/font/%E4%B8%AD%E6%96%87/%E9%98%BF%E9%87%8C%E5%B7%B4%E5%B7%B4%E6%99%AE%E6%83%A0%E4%BD%93/Alibaba-PuHuiTi-Heavy.ttf"
  "Alibaba-PuHuiTi-Bold.ttf|https://raw.githubusercontent.com/wordshub/free-font/master/assets/font/%E4%B8%AD%E6%96%87/%E9%98%BF%E9%87%8C%E5%B7%B4%E5%B7%B4%E6%99%AE%E6%83%A0%E4%BD%93/Alibaba-PuHuiTi-Bold.ttf https://fastly.jsdelivr.net/gh/wordshub/free-font@master/assets/font/%E4%B8%AD%E6%96%87/%E9%98%BF%E9%87%8C%E5%B7%B4%E5%B7%B4%E6%99%AE%E6%83%A0%E4%BD%93/Alibaba-PuHuiTi-Bold.ttf ghapi:https://api.github.com/repos/wordshub/free-font/contents/assets/font/%E4%B8%AD%E6%96%87/%E9%98%BF%E9%87%8C%E5%B7%B4%E5%B7%B4%E6%99%AE%E6%83%A0%E4%BD%93/Alibaba-PuHuiTi-Bold.ttf"
  "PangMenZhengDao.ttf|https://raw.githubusercontent.com/wordshub/free-font/master/assets/font/%E4%B8%AD%E6%96%87/%E5%BA%9E%E9%97%A8%E6%AD%A3%E9%81%93%E5%AD%97%E4%BD%93%E7%B3%BB%E5%88%97/%E5%BA%9E%E9%97%A8%E6%AD%A3%E9%81%93%E6%A0%87%E9%A2%98%E4%BD%93.ttf https://fastly.jsdelivr.net/gh/wordshub/free-font@master/assets/font/%E4%B8%AD%E6%96%87/%E5%BA%9E%E9%97%A8%E6%AD%A3%E9%81%93%E5%AD%97%E4%BD%93%E7%B3%BB%E5%88%97/%E5%BA%9E%E9%97%A8%E6%AD%A3%E9%81%93%E6%A0%87%E9%A2%98%E4%BD%93.ttf ghapi:https://api.github.com/repos/wordshub/free-font/contents/assets/font/%E4%B8%AD%E6%96%87/%E5%BA%9E%E9%97%A8%E6%AD%A3%E9%81%93%E5%AD%97%E4%BD%93%E7%B3%BB%E5%88%97/%E5%BA%9E%E9%97%A8%E6%AD%A3%E9%81%93%E6%A0%87%E9%A2%98%E4%BD%93.ttf"
  "站酷高端黑.ttf|https://raw.githubusercontent.com/wordshub/free-font/master/assets/font/%E4%B8%AD%E6%96%87/%E7%AB%99%E9%85%B7%E5%AD%97%E4%BD%93%E7%B3%BB%E5%88%97/%20%E7%AB%99%E9%85%B7%E9%AB%98%E7%AB%AF%E9%BB%91.ttf https://fastly.jsdelivr.net/gh/wordshub/free-font@master/assets/font/%E4%B8%AD%E6%96%87/%E7%AB%99%E9%85%B7%E5%AD%97%E4%BD%93%E7%B3%BB%E5%88%97/%20%E7%AB%99%E9%85%B7%E9%AB%98%E7%AB%AF%E9%BB%91.ttf ghapi:https://api.github.com/repos/wordshub/free-font/contents/assets/font/%E4%B8%AD%E6%96%87/%E7%AB%99%E9%85%B7%E5%AD%97%E4%BD%93%E7%B3%BB%E5%88%97/%20%E7%AB%99%E9%85%B7%E9%AB%98%E7%AB%AF%E9%BB%91.ttf"
  "站酷庆科黄油体.ttf|https://raw.githubusercontent.com/wordshub/free-font/master/assets/font/%E4%B8%AD%E6%96%87/%E7%AB%99%E9%85%B7%E5%AD%97%E4%BD%93%E7%B3%BB%E5%88%97/%E7%AB%99%E9%85%B7%E5%BA%86%E7%A7%91%E9%BB%84%E6%B2%B9%E4%BD%93.ttf https://fastly.jsdelivr.net/gh/wordshub/free-font@master/assets/font/%E4%B8%AD%E6%96%87/%E7%AB%99%E9%85%B7%E5%AD%97%E4%BD%93%E7%B3%BB%E5%88%97/%E7%AB%99%E9%85%B7%E5%BA%86%E7%A7%91%E9%BB%84%E6%B2%B9%E4%BD%93.ttf ghapi:https://api.github.com/repos/wordshub/free-font/contents/assets/font/%E4%B8%AD%E6%96%87/%E7%AB%99%E9%85%B7%E5%AD%97%E4%BD%93%E7%B3%BB%E5%88%97/%E7%AB%99%E9%85%B7%E5%BA%86%E7%A7%91%E9%BB%84%E6%B2%B9%E4%BD%93.ttf"
  "YouSheBiaoTiHei.ttf|https://raw.githubusercontent.com/wordshub/free-font/master/assets/font/%E4%B8%AD%E6%96%87/%E5%85%B6%E4%BB%96%E5%AD%97%E4%BD%93/%E4%BC%98%E8%AE%BE%E6%A0%87%E9%A2%98%E9%BB%91.ttf https://fastly.jsdelivr.net/gh/wordshub/free-font@master/assets/font/%E4%B8%AD%E6%96%87/%E5%85%B6%E4%BB%96%E5%AD%97%E4%BD%93/%E4%BC%98%E8%AE%BE%E6%A0%87%E9%A2%98%E9%BB%91.ttf ghapi:https://api.github.com/repos/wordshub/free-font/contents/assets/font/%E4%B8%AD%E6%96%87/%E5%85%B6%E4%BB%96%E5%AD%97%E4%BD%93/%E4%BC%98%E8%AE%BE%E6%A0%87%E9%A2%98%E9%BB%91.ttf"
  "LXGWWenKaiScreen.ttf|https://github.com/lxgw/LxgwWenKai-Screen/releases/download/v1.522/LXGWWenKaiScreen.ttf"
  "SmileySans-Oblique.ttf|ghapi:https://api.github.com/repos/JACKADUX/Godot-Audio-Player/contents/resource/font/SmileySans/SmileySans-Oblique.ttf https://fastly.jsdelivr.net/gh/JACKADUX/Godot-Audio-Player@master/resource/font/SmileySans/SmileySans-Oblique.ttf zip:https://github.com/atelier-anchor/smiley-sans/releases/download/v2.0.1/smiley-sans-v2.0.1.zip"
)

EXTRA_FONTS=(
  "HarmonyOS_Sans_SC.ttf|zip:https://alliance-communityfile-drcn.dbankcdn.com/FileServer/getFile/cmtyManage/011/111/111/0000000000011111111.20260611171743.77886644144213121813005934094365:50001231000000:2800:0CCF575ADA0FCAD85EE25909C15C402A40FA94ABCCFEFC5BD37061A6B94239FF.zip"
)

# ---- 下载器：curl → wget → python3（任何一个可用即可） ----
DL=""
for tool in curl wget python3; do
  command -v "$tool" >/dev/null 2>&1 && { DL="$tool"; break; }
done
[[ -z "$DL" ]] && { warn "服务器上没有 curl/wget/python3，无法下载字体（可手动上传字体到 $FONT_DIR）"; }

# ---- 本机代理自动探测：直连失败时用（有代理的服务器常见） ----
PROXY=""
detect_proxy() {
  local port
  for port in 7890 7891 1080 10809 10808 8118 8889 2080 20171; do
    if (echo > "/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1; then
      PROXY="http://127.0.0.1:$port"
      return 0
    fi
  done
  return 1
}

# 下载一个 URL → $2；失败时把原因写入全局 DL_ERR
DL_ERR=""
dl_one() {
  local url="$1" out="$2" proxy_arg="" env_arg=()
  [[ -n "$PROXY" ]] && proxy_arg="$PROXY"
  local ghapi=0
  [[ "$url" == ghapi:* ]] && { ghapi=1; url="${url#ghapi:}"; }
  case "$DL" in
    curl)
      local err
      err="$(curl -fsSL --retry 1 --retry-delay 2 --max-time 600 ${proxy_arg:+--proxy "$proxy_arg"} \
        ${ghapi:+-H "Accept: application/vnd.github.raw"} -o "$out" "$url" 2>&1 >/dev/null)"
      [[ -s "$out" ]] && return 0
      DL_ERR="${err%%$'\n'*}"
      ;;
    wget)
      local err
      err="$(wget -q --timeout=60 --tries=2 ${proxy_arg:+-e use_proxy=yes -e https_proxy="$proxy_arg"} \
        ${ghapi:+--header="Accept: application/vnd.github.raw"} -O "$out" "$url" 2>&1)"
      [[ -s "$out" ]] && return 0
      DL_ERR="${err%%$'\n'*}"
      ;;
    python3)
      env_arg=()
      [[ -n "$PROXY" ]] && env_arg=(env "https_proxy=$PROXY" "http_proxy=$PROXY")
      local err
      err="$("${env_arg[@]}" python3 - "$url" "$out" <<'PY' 2>&1
import sys, urllib.request, shutil
url, out = sys.argv[1], sys.argv[2]
try:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.raw", "User-Agent": "coco-fonts"})
    with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f:
        shutil.copyfileobj(r, f)
except Exception as exc:
    print(f"{type(exc).__name__}: {exc}")
    sys.exit(1)
PY
)"
      [[ -s "$out" ]] && return 0
      DL_ERR="${err%%$'\n'*}"
      ;;
    *)
      DL_ERR="没有可用的下载工具（curl/wget/python3 均缺失）"
      ;;
  esac
  return 1
}

# ---- 先判断"有没有活干"：都已就绪就直接返回，不要密码、不打扰 ----
# 渲染器是否就绪
NEED_RENDERER=0
command -v rsvg-convert >/dev/null 2>&1 || NEED_RENDERER=1

# 字体是否齐（下面主流程会用同一份清单）
ALL=("${CORE_FONTS[@]}")
[[ "${COCO_FONTS_EXTRA:-0}" == "1" ]] && ALL+=("${EXTRA_FONTS[@]}")
count_fonts() { find "$FONT_DIR" -maxdepth 1 -type f \( -iname '*.ttf' -o -iname '*.otf' \) 2>/dev/null | wc -l | tr -d ' '; }
count_missing() {
  local n=0 entry
  for entry in "${ALL[@]}"; do
    [[ -s "$FONT_DIR/${entry%%|*}" ]] || n=$((n + 1))
  done
  echo "$n"
}
NEED_FONTS="$(count_missing)"

if [[ "$NEED_RENDERER" -eq 0 && "$NEED_FONTS" -eq 0 ]]; then
  info "渲染器已就绪：$(rsvg-convert --version 2>/dev/null | head -1)"
  ok "海报字体已就绪（$(count_fonts) 个文件，$(du -sh "$FONT_DIR" 2>/dev/null | cut -f1)）"
  exit 0
fi

# ---- 确实有活干，才一次性拿 sudo（避免下载/安装途中再弹密码，也避免没事还问密码） ----
if [[ "$(id -u)" != "0" ]] && ! sudo -n true 2>/dev/null; then
  say "海报组件需要系统权限：接下来要输入服务器密码（输入时屏幕不显示字符，属正常）"
  sudo -v || warn "未获得 sudo 授权，将尽力继续（渲染器/字体可能装不上）"
fi

# ---- 渲染器 ----
if [[ "$NEED_RENDERER" -eq 1 ]]; then
  say "安装海报渲染器（librsvg2-bin，约 7MB）..."
  if sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q librsvg2-bin 2>&1 | tail -2; then
    if command -v rsvg-convert >/dev/null 2>&1; then
      ok "渲染器就绪：$(rsvg-convert --version 2>/dev/null | head -1)"
    else
      warn "渲染器未生效（海报会回落旧版排版引擎）"
    fi
  else
    warn "渲染器安装失败（网络或软件源不可用）——海报会回落旧版排版引擎"
  fi
else
  info "渲染器已就绪：$(rsvg-convert --version 2>/dev/null | head -1)"
fi

sudo mkdir -p "$FONT_DIR" 2>/dev/null || mkdir -p "$FONT_DIR" 2>/dev/null || true
mkdir -p "$TMP"

# 返回 0=本次装上；1=失败；2=已存在
fetch_one() {
  local name="$1" urls="$2" tmpfile url
  [[ -s "$FONT_DIR/$name" ]] && return 2
  tmpfile="$TMP/$name"
  for url in $urls; do
    if [[ "$url" == zip:* ]]; then
      local zurl="${url#zip:}"
      dl_one "$zurl" "$TMP/pkg.zip" || continue
      (cd "$TMP" && unzip -o -q "$TMP/pkg.zip") >/dev/null 2>&1 || continue
      local found
      found="$(find "$TMP" -type f \( -iname '*.ttf' -o -iname '*.otf' \) 2>/dev/null | grep -iv '__MACOSX' | head -1)"
      [[ -n "$found" ]] && cp -f "$found" "$tmpfile"
    else
      dl_one "$url" "$tmpfile" || continue
    fi
    [[ -s "$tmpfile" ]] && break
  done
  # 校验字体文件头（sfnt: 00010000 / OTTO / true），防止把错误页当字体装进去
  if [[ -s "$tmpfile" ]]; then
    local magic
    magic=$(head -c 4 "$tmpfile" | od -An -tx1 | tr -d ' \n')
    if [[ "$magic" == "00010000" || "$magic" == "4f54544f" || "$magic" == "74727565" ]]; then
      sudo cp -f "$tmpfile" "$FONT_DIR/$name" 2>/dev/null || cp -f "$tmpfile" "$FONT_DIR/$name" 2>/dev/null
      [[ -s "$FONT_DIR/$name" ]] && return 0
      DL_ERR="文件已下载但写入 $FONT_DIR 失败（权限问题）"
    else
      DL_ERR="下载内容不是字体文件（可能被网络拦截或返回了错误页）"
    fi
  fi
  [[ -z "$DL_ERR" ]] && DL_ERR="下载失败（网络不可达或超时）"
  return 1
}

# ---- 优先走 Gitee 字体包（一个文件，国内服务器更快更稳） ----
install_bundle() {
  local name="$1" tmp="$TMP/$1" ex="$TMP/bundle_ex" n=0 f
  say "下载字体包 $name ..."
  if dl_one "$BUNDLE_BASE/$name" "$tmp"; then
    rm -rf "$ex"; mkdir -p "$ex"
    if tar xzf "$tmp" -C "$ex" 2>/dev/null; then
      # 不假设包内目录层级：把解出来的字体统一装进 FONT_DIR
      while IFS= read -r f; do
        [[ -s "$f" ]] || continue
        if sudo cp -f "$f" "$FONT_DIR/$(basename "$f")" 2>/dev/null || cp -f "$f" "$FONT_DIR/$(basename "$f")" 2>/dev/null; then
          n=$((n + 1))
        fi
      done < <(find "$ex" -type f \( -iname '*.ttf' -o -iname '*.otf' \) 2>/dev/null)
      if [[ $n -gt 0 ]]; then
        fc-cache -f >/dev/null 2>&1 || true
        ok "字体包已安装 $n 个字体（当前共 $(count_fonts) 个文件）"
        return 0
      fi
      DL_ERR="字体包内没有字体文件"
    else
      DL_ERR="字体包解压失败"
    fi
  fi
  warn "字体包不可用（${DL_ERR}），改用逐个字体下载"
  return 1
}

# ---- 先探测 GitHub raw 是否可达，决定"优先哪个源" ----
# 可达（海外网络）→ 先逐个下载（快）；不可达（国内常见：raw 域名被污染）→ 先试 Gitee 字体包
RAW_OK=0
if command -v curl >/dev/null 2>&1; then
  if timeout 8 curl -fsS -o /dev/null --max-time 8 \
       "https://raw.githubusercontent.com/wordshub/free-font/master/README.md" 2>/dev/null; then
    RAW_OK=1
  fi
fi

missed=()
if [[ "$(count_missing)" -eq 0 ]]; then
  info "海报字体已就绪（$(count_fonts) 个文件，$(du -sh "$FONT_DIR" 2>/dev/null | cut -f1)）"
else
  if [[ $RAW_OK -eq 0 ]]; then
    say "GitHub raw 不可达（国内常见）→ 优先使用 Gitee 字体包"
    install_bundle "$BUNDLE_CORE" || true
    [[ "${COCO_FONTS_EXTRA:-0}" == "1" ]] && install_bundle "$BUNDLE_EXTRA" || true
    fc-cache -f >/dev/null 2>&1 || true
  else
    say "GitHub 源可达（海外网络）→ 优先逐个下载字体"
  fi

  todo=$(count_missing)
  if [[ "$todo" -gt 0 ]]; then
    say "下载海报字体：缺 $todo 个（合计约 $((todo * 12))MB，视网速 2~10 分钟；可 Ctrl+C 中断，下次重跑会跳过已装部分）"
    i=0
    for entry in "${ALL[@]}"; do
      name="${entry%%|*}"; urls="${entry#*|}"
      [[ -s "$FONT_DIR/$name" ]] && continue      # 已装好：安静跳过，不刷屏
      i=$((i + 1))
      if fetch_one "$name" "$urls"; then
        say "  [$i/$todo] $name ... 已安装（$(du -h "$FONT_DIR/$name" 2>/dev/null | cut -f1)）"
      else
        # 直连失败 → 探测本机代理再试一次
        if [[ -z "$PROXY" ]] && detect_proxy; then
          say "  [$i/$todo] $name ... 直连失败，改用本机代理 $PROXY 重试"
          fetch_one "$name" "$urls" && { say "      → 代理下载成功（$(du -h "$FONT_DIR/$name" 2>/dev/null | cut -f1)）"; continue; }
        fi
        say "  [$i/$todo] $name ... 未装成功：${DL_ERR}"
        missed+=("$name")
      fi
    done
  fi
fi

# 逐个下载仍失败 → 兜底再试 Gitee 字体包（海外也可能个别源不通）
if [[ ${#missed[@]} -gt 0 && $RAW_OK -eq 1 ]]; then
  info "逐个下载有失败项，兜底尝试 Gitee 字体包..."
  if install_bundle "$BUNDLE_CORE"; then
    missed=()
    for entry in "${ALL[@]}"; do
      [[ -s "$FONT_DIR/${entry%%|*}" ]] || missed+=("${entry%%|*}")
    done
  fi
fi

fc-cache -f >/dev/null 2>&1 || true
count=$(find "$FONT_DIR" -maxdepth 1 -type f \( -iname '*.ttf' -o -iname '*.otf' \) 2>/dev/null | wc -l | tr -d ' ')
size=$(du -sh "$FONT_DIR" 2>/dev/null | cut -f1)

if [[ ${#missed[@]} -gt 0 ]]; then
  warn "未装成功：${missed[*]}"
  echo "  海报仍可正常出图（会回落系统自带字体，观感差一些）。想装上的话按下面排查："
  echo "    1) 看下载工具：command -v curl wget python3   （都没有就 apt-get install -y curl）"
  echo "    2) 看网络：curl -sSI --max-time 10 https://raw.githubusercontent.com/wordshub/free-font/master/README.md | head -1"
  echo "    3) 服务器若有代理：export https_proxy=http://127.0.0.1:端口 && bash scripts/install_fonts.sh"
  echo "    4) 也可把这台机器上已装好的字体目录拷过来：/usr/local/share/fonts/coco"
else
  ok "海报字体已就绪（${count} 个文件，${size}）"
fi
exit 0
