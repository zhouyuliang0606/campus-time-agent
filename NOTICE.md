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
| `app/static/bg/park.jpg` | **未知** | **未确认** | 参赛者本人提供的照片，来源未追溯 |
| `app/static/bg/space.jpg` | **未知** | **未确认** | 参赛者本人提供的图片（450×300），来源未追溯 |

⚠️ **`park.jpg` 与 `space.jpg` 的授权尚未落实**。这两张是参赛者自己贴进来的图，来源没追溯过。
仓库是公开的、要交比赛，**推送或公开前必须二选一**：确认到可自由使用的授权并补进本表，
或者删掉这两个文件（学生端「设置 → 背景」里的「草坪」「星空」两档要同时摘掉，
`tests/test_44_glass_ui_and_bg.py` E 批会跟着红，那是它在正常工作）。
`forest.jpg` 与 `milky.jpg` 许可明确，可留。

四张图都已缩到 1760px 以内长边、progressive JPEG（163 / 204 / 176 / 13 KB），未做裁剪以外的修改。
`milky.jpg` 依 CC BY 4.0 要求署名如上；如需替换为无署名要求的素材，删除该文件并同步更新本表与学生端「设置 → 背景」里的选项。
`space.jpg` 原图只有 450×300，铺到 1440 宽是 3.2 倍放大、星点会糊成光斑，**不适合当正式演示背景**。

## 界面图标

线性图标为手写 SVG（`viewBox` 24×24、`stroke-width:1.7`），风格参考
[Lucide](https://lucide.dev)（ISC 许可）的日历、复选框、滑块、盒子、碗、上传、问号、麦克风、回形针等图形，
未复制其源码文件。
