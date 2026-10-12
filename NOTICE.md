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
| `app/static/bg/deep.jpg` | 本项目自生成（Qoder 图像生成，非取自任何图库） | **无第三方许可、无署名义务** | 本地生成，见 `ai-prompts/2026-10-12-glass-ui-rain.md` §7 |
| `app/static/bg/dusk.jpg` | 本项目自生成（同上） | **无第三方许可、无署名义务** | 本地生成，见同上 |

## 「须署名」到底要做什么

CC BY 4.0 的署名义务是**在作品某处写明作者、许可证、来源链接、以及是否改过**，
不要求出现在界面角落。本项目按第 3 行的做法在本文件里履行：
`milky.jpg` 的作者是 ESO / S. Guisard，许可 CC BY 4.0，来源是上面那条 Commons 链接，
做过裁剪与压缩。界面（含学生端）不显示任何署名，这是 glass-ui-skill 的硬性规定，
与署名义务不冲突 —— 义务落在文档，不落在像素。

实际代价只有一种：**别人二次分发这份作品时，也必须带上这行署名**。
不想背这条的，用 `deep.jpg` / `dusk.jpg` 两档（自生成，零义务），
或直接删掉 `milky.jpg` 并把「银河」那一档摘掉。


**已删除的素材（2026-10-12）**：`park.jpg`（草坪）与 `space.jpg`（星空）是随手贴进来的，
作者与来源都没追溯清楚，许可核不了 —— 仓库公开、且要交比赛，这种素材不能留。
两个文件已从仓库删除，学生端「设置 → 背景」里的「草坪」「星空」两档同时摘掉，
补上了自生成的「深空」「星野」两档，现在是 **雾松林 / 深空 / 星野 / 银河 / 不用图** 五档。
（曾存过 `ct_bg=park` 的浏览器不会白屏：那张表是白名单查表，查不到就回落 `forest`。）

⚠️ 留一张加一道闸：`tests/test_44_glass_ui_and_bg.py` 会扫 `app/static/bg/` 里的**每个**文件，
要求它在本表登记过、且许可列不出现"未确认"。以后再往这个目录贴一张没查证的图，测试当场红。

四张图都缩到 1760px 以内长边、progressive JPEG（164 / 205 / 139 / 85 KB）。
`milky.jpg` 依 CC BY 4.0 要求署名如上。
两张自生成图出图时右下角带「Qoder AI 生成」的平台角标，处理时**裁掉了底部 100px 那条**
把它一并去掉（玻璃质感要求界面上不许出现水印，见 glass-ui-skill）；
这件事记在这里而不是藏着，是为了来源可查：裁前原图 1792×1024，裁后 1760×908。


## 界面图标

线性图标为手写 SVG（`viewBox` 24×24、`stroke-width:1.7`），风格参考
[Lucide](https://lucide.dev)（ISC 许可）的日历、复选框、滑块、盒子、碗、上传、问号、麦克风、回形针等图形，
未复制其源码文件。
