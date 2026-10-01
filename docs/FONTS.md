# 海报字体与渲染器（授权与来源）

Coco 生成房源海报时会用到以下字体与渲染器，脚本 `scripts/install_fonts.sh` 会自动安装。
**所有字体均为免费商用**（开源协议或字体作者/厂商官方声明的免费商用），不使用任何商业字库。
分发本产品时请连带保留本文件，以便使用者核对授权。

## 渲染器

| 组件 | 用途 | 许可 | 来源 |
|---|---|---|---|
| `librsvg2-bin`（rsvg-convert） | 把 SVG 海报渲染成 PNG | LGPL-2.1+ | Ubuntu 官方源（apt） |

## 字体

| 字体 | 用途 | 许可 | 来源 |
|---|---|---|---|
| 思源黑体 Noto Sans CJK SC（Regular/Bold/Black） | 正文、标签、数字 | SIL Open Font License 1.1 | notofonts/noto-cjk |
| 思源宋体 Noto Serif CJK SC Black | 极简高级款标题 | SIL Open Font License 1.1 | notofonts/noto-cjk |
| 得意黑 Smiley Sans | 预留（多套清单款重做时用） | SIL Open Font License 1.1 | atelier-anchor/smiley-sans |
| 霞鹜文楷 LXGW WenKai Screen | 中式雅致标题/正文 | SIL Open Font License 1.1 | lxgw/LxgwWenKai-Screen |
| 阿里巴巴普惠体 Heavy / Bold | 促销款大标题与价格数字 | 阿里巴巴普惠体官方声明免费商用（含嵌入式使用） | fonts.alibabagroup.com（镜像分发） |
| 庞门正道标题体 | 硬朗标题备选（预留） | 作者声明免费商用 | 公开镜像 |
| 站酷高端黑 / 站酷庆科黄油体 | 极简清冷标题 / 租房活泼标题 | 站酷官方声明免费商用 | zcool.com.cn/special/zcoolfonts（镜像分发） |
| 优设标题黑 | 促销标题备选 | 优设官方声明免费商用 | 公开镜像 |
| MiSans（可选） | 现代黑体多字重 | 小米官方声明免费商用 | 小米官方 CDN |
| 鸿蒙黑体 HarmonyOS Sans SC（可选） | 通用黑体 | 华为官方声明免费商用 | 华为官方 CDN |

（可选字体默认不安装：`COCO_FONTS_EXTRA=1 bash scripts/install_fonts.sh` 再装。）

## 说明

- 字体缺失时海报**不会失败**：渲染器与字体都做了多级回退，最差情况用系统自带中文字体出图，只是观感下降。
- 字体文件不随本仓库分发，安装脚本从上述来源按 URL 下载（多源自动回退）。
- 如某字体授权条款日后变更，请以字体官方页面为准。

## 下载源与字体包

安装脚本按这个顺序取字体：

1. **Gitee 字体包**（首选，一个文件装齐，中国大陆服务器最快）：
   `https://gitee.com/LYH-Gini/coco-real-estate-agent/releases/download/fonts-v1/coco_fonts_core_v1.tar.gz`
   （扩展包 `coco_fonts_extra_v1.tar.gz`：`COCO_FONTS_EXTRA=1` 时使用）
2. **逐字体下载**：jsDelivr / GitHub API 原始内容（api.github.com）/ GitHub raw 多源回退（部分服务器屏蔽 raw.githubusercontent.com，此时走前两条）

> 注：部分中国大陆服务器会屏蔽 `raw.githubusercontent.com`（连接立即失败），
> 这时 Gitee 字体包或 jsDelivr 就是有效路径；两者都不通也不会阻塞 —— 海报会回落系统字体。
