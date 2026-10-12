# NOTICE — 第三方素材与出处

本文件登记仓库内**非本项目原创**的部分。学生页面上不显示任何署名或水印；署名义务在这里履行。

## 界面样式层

- `app/static/glass.css` 的玻璃质感配方（主题 token、反光/磨砂两套质感、摄影层 veil、动效降级）
  由本项目自研技能包 **glass-ui-skill** 整理而来，其配方派生自
  [`gdut4140/summer-checkin-ui-skill`](https://github.com/gdut4140/summer-checkin-ui-skill)（MIT）。

## 背景图片

| 文件 | 作者 | 许可 | 来源 |
|---|---|---|---|
| `app/static/bg/forest.jpg` | Vadim Sherbakov（madebyvadim） | **CC0**（无署名义务，仍致谢） | [Wikimedia Commons: File:Foggy pine (Unsplash).jpg](https://commons.wikimedia.org/wiki/File:Foggy_pine_(Unsplash).jpg) |
| `app/static/bg/milky.jpg` | **ESO / S. Guisard**（www.eso.org/~sguisard） | **CC BY 4.0**（须署名） | [Wikimedia Commons: File:Guisard - Milky Way.jpg](https://commons.wikimedia.org/wiki/File:Guisard_-_Milky_Way.jpg) |

**已删除的素材（2026-10-12）**：`park.jpg`（草坪）与 `space.jpg`（星空）是随手贴进来的，
作者与来源都没追溯清楚，许可核不了 —— 仓库公开、且要交比赛，这种素材不能留。
两个文件已从仓库删除，学生端「设置 → 背景」里的「草坪」「星空」两档同时摘掉，
现在只剩 **雾松林 / 银河 / 不用图** 三档。
（曾存过 `ct_bg=park` 的浏览器不会白屏：那张表是白名单查表，查不到就回落 `forest`。）

⚠️ 留一张加一道闸：`tests/test_44_glass_ui_and_bg.py` 会扫 `app/static/bg/` 里的**每个**文件，
要求它在本表登记过、且许可列不出现"未确认"。以后再往这个目录贴一张没查证的图，测试当场红。

两张留下的图都缩到 1760px 以内长边、progressive JPEG（164 / 205 KB），未做裁剪以外的修改。
`milky.jpg` 依 CC BY 4.0 要求署名如上。

## 界面图标

线性图标为手写 SVG（`viewBox` 24×24、`stroke-width:1.7`），风格参考
[Lucide](https://lucide.dev)（ISC 许可）的日历、复选框、滑块、盒子、碗、上传、问号、麦克风、回形针等图形，
未复制其源码文件。
