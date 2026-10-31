/* 背景图白名单（2026-10-31 从 student.html 的 GX_BGS 里抽出来的**只有 key→图片路径**这一层）。
 *
 * 为什么要单独一个文件：`/plan` 也要吃到学生端那张背景图，而它不该再抄一份表 ——
 * 同一件事抄两份就是这个项目反复发病的根（署名义务、撤过的图都会跟着漂）。
 * ⚠️ 但**旋钮反解值、档名、署名文案仍只在 student.html 的 GX_BGS 里**（那是设置页要用的），
 *    这里只放"给我 key，我给你 src"。两边必须逐项相等，由 test_44 I 批钉住。
 *
 * 查不到 key 就回落 forest（**不是拼路径**）：老值 `ct_bg=park` / `space` 那两张
 * 2026-10-12 因许可核不了撤了（见 NOTICE.md），拼路径会得到一个 404 的白屏。
 */
window.CT_BG_SRC = {
  forest: "/static/bg/forest.jpg",
  deep: "/static/bg/deep.jpg",
  dusk: "/static/bg/dusk.jpg",
  milky: "/static/bg/milky.jpg"
};
window.CT_BG_FALLBACK = "forest";
