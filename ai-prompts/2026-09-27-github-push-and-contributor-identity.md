# 2026-09-27 · 首次推送 GitHub 公开仓库 + 提交身份归属查证

> 关联 commit：本批 1 个 docs commit（快照 + README 索引，与代码分开提交）
> 主题：把 188 个本地细粒度提交推上公开仓库；查明「Contributors 显示 zhouto」的成因并修正后续提交身份

---

## 一、用户 Prompt（原话摘录）

| # | 原话 |
|---|---|
| 1 | 「传github」 |
| 2 | 「储存库访问选哪个」 |
| 3 | 「这个contribors为什么是zhouto」 |
| 4 | 「为什么我提交是以zhouto的身份？」 |
| 5 | 「1吧」（在四个方案中选择"只改以后的提交"） |

---

## 二、AI 研判

### 2.1 「传github」为什么一开始推不上去

三个**互相独立**的问题叠在一起，缺一个都推不动。诊断顺序必须是「网络 → 仓库 → 凭据」：

| 卡点 | 现象 | 真因 | 解法 |
|---|---|---|---|
| 网络 | `Empty reply from server` / `schannel: server closed abruptly` | WorkBuddy 自带透明代理 `127.0.0.1:54437` **拦截 github.com** | 改走用户自有 VPN 代理 `http://127.0.0.1:7890`，并对 git/curl 显式传参 |
| TLS | `CRYPT_E_REVOCATION_OFFLINE (0x80092013)` | Windows schannel 联网查证书吊销列表失败 | `curl` 加 `--ssl-no-revoke`（`api.github.com` 必加） |
| 仓库 | `Repository not found`，且**不弹任何认证提示** | 远程仓库**根本还没创建**（账号 200、仓库 404） | 先用 API `POST /user/repos` 建公开仓库 |
| 凭据 | `git credential fill` 直接卡死（SIGTERM） | 全局 helper 是 GCM，本机**从没登录过**，会弹 GUI 阻塞 | 弃用 GCM，改用 git 自带 `store` 助手 |

**关键判断**：`remote: Repository not found.` 不带认证提示，容易被误读成"没权限"。实际上未认证访问**不存在的仓库**和**私有仓库**都返回 404，要先打 `/users/<owner>` 确认账号在不在。

### 2.2 「Contributors 为什么是 zhouto」

排查链（逐项取证，不猜）：

1. `git log --format='%an <%ae>' | sort -u` → **188 个提交署名全部是 `zhouy <zhouyig@163.com>`**，无一例外。
2. `.git/config`：`user.name = zhouy`、`user.email = zhouyig@163.com`；`~/.gitconfig`：`user.name = zhouto`、同邮箱（仓库级覆盖全局）。
3. 调 GitHub API 看该提交的归属：`author.login = zhouto`（id `29192811`）。
4. 查两个账号：`zhouto` 创建于 **2017-06-05**，`zhouyuliang0606` 创建于 **2026-08-03**——仓库挂在后者名下。

**结论（机制层面）**：

- **commit 里没有"账号"这个概念**，只有 `name` 和 `email` 两个自由文本字段，由本地 git 自己写，GitHub 收下时不做校验。
- 归属动作发生在**网页渲染那一刻**：GitHub 拿 `email` 去反查已验证账号。查到 → 显示该账号 login + 头像并计入 Contributors；查不到 → 只显示纯文本名字、**不计入任何贡献统计**。
- 因此 **`user.name` 完全不参与归属判断**——反证就在眼前：提交里 name 明明写的是 `zhouy`，显示出来却是 `zhouto`。
- `zhouyig@163.com` 是 2017 年绑给 zhouto 的老邮箱，而 **GitHub 规定一个邮箱只能绑一个账号**，所以 2026 年新建的 zhouyuliang0606 名下没有它，认领不到这些提交。

**推论**：谁拥有那个邮箱，提交就算谁的——这也是伪造提交能存在的原因。**commit 署名是声明，不是凭证。**

### 2.3 方案取舍（给用户的四个选项）

| 方案 | 做法 | 哈希是否变化 | 否决/采纳理由 |
|---|---|---|---|
| ① 只改以后的提交 | 换 `user.email` 为目标账号邮箱 | 不变 | **采纳**：改动最小、不动历史、不碰账号设置 |
| ② 换绑邮箱 | 把老邮箱挪到新账号 | 不变（实时反查） | 需先从 zhouto 移除该邮箱（它是主邮箱），且 zhouto 名下其他仓库提交会跟着改归属 |
| ③ 转移仓库到 zhouto | 改 repo owner | 不变 | 仓库地址变更，与既定提交地址不符 |
| ④ 重写历史 | `filter-repo` + force push | **全部变化** | **明确否决**：`ai-prompts/` 中大量引用 commit 短哈希（`2da7b7a`、`ba022ce`、`e744183` 等），而这些正是比赛要交的材料，重写会导致引用全部失准 |

---

## 三、改动

### 3.1 远端仓库（GitHub 侧）

- 新建公开仓库 `zhouyuliang0606/campus-time-agent`（`POST /user/repos`，`private:false`、**`auto_init:false`**——否则会产生一条远程提交导致首推 non-fast-forward）。
- 建库后默认分支是 `main`，本地是 `master` → 推完用 `PATCH /repos/{owner}/{repo}` 把 `default_branch` 改回 `master`，否则仓库首页显示不出来。

> ⚠️ 用户提供的 PAT **仅存在于仓库外的临时文件与当次进程环境变量中**，不写入本仓库、不写入 `.git/config`、不进入任何提交内容。

### 3.2 推送手法（不落密钥）

```bash
git -c credential.helper= \
    -c credential.helper="store --file=<仓库外的临时凭据文件>" \
    -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 \
    push -u origin master
```

关键在 **`-c credential.helper=`（空值）会清空继承来的 helper 列表**——不加这句，全局的 GCM 仍会插进链条并弹 GUI 把命令挂死。

### 3.3 推送前安全闸（先做，推出去就删不干净）

| 扫描 | 命令 | 结果 |
|---|---|---|
| 跟踪的敏感文件 | `git ls-files` + 正则 | 无 `.env` / `settings.json` / `data/student/` / `uploads/` |
| 全历史文件名 | `git log --all --name-only` | 干净，从未出现过 |
| 内容级密钥 | `git grep -E "sk-…|ghp_…|github_pat_…"` | 3 处命中**全是占位符**：`.env.example` 注释、`ai-logs/09` 文档、`tests/test_02_upload.py:55` 的 `sk-abcdefghijklmnop1234` 假值 |

**注意**：`.gitignore` 只对"从未被跟踪过"的文件有效。文件一旦进过历史，加 ignore 也没用，只能 `git filter-repo` 洗历史——所以这道闸**必须在第一次 push 之前**做。

### 3.4 后续提交身份修正（本批唯一改动）

```bash
git config --local user.name  "zhouyuliang0606"
git config --local user.email "312324985+zhouyuliang0606@users.noreply.github.com"
```

- 用 GitHub 的**免回复邮箱**（格式 `<ID>+<login>@users.noreply.github.com`），**用户无需在 GitHub 上绑定任何真实邮箱**。
- 只改**仓库级**配置，`~/.gitconfig` 保持原样，不影响本机其他项目。
- **历史 188 个提交一字未动**（哈希不变），只影响此后新增的提交。

---

## 四、验证

### 4.1 推送一致性（三项全过）

| 检查项 | 结果 |
|---|---|
| 本地 HEAD / 远程 master 哈希 | `f8582e17ec936c513a1240370d0a203863168fda` **完全相同** |
| 提交数 | 本地 **188** = 远程 **188** |
| `git diff HEAD origin/master` | **为空** |
| 线上根目录 | `README.md` `EXPLAIN.md` `.env.example` `.gitignore` `requirements.txt` + `app/ ai-logs/ ai-prompts/ docs/ samples/ tests/` 齐全 |
| 线上 `ai-prompts/` | **19 个快照文件** |
| 仓库可见性 | Public，未登录访问返回 HTTP 200 |

### 4.2 配置生效性

| 位置 | 值 |
|---|---|
| 仓库级 `user.name` / `user.email` | `zhouyuliang0606` / `312324985+zhouyuliang0606@users.noreply.github.com` |
| 全局 `user.name` / `user.email` | `zhouto` / `zhouyig@163.com`（**未改动**） |
| 历史 188 个提交署名 | `zhouy <zhouyig@163.com>`（**未改动**） |

---

## 五、沉淀

- 新增用户级技能 `github-push-via-proxy`：把「代理 / ssl 开关 / 建库参数 / PAT 权限清单 / store 凭据助手 / 推前安全闸 / 推后三项核验」固化为可复用流程。
  - 其中一条**容易踩的坑**已写进技能：fine-grained PAT 的 **Repository access 必须选 `All repositories`**——因为要用 API 新建的仓库**还不存在**，`Only select repositories` 根本选不到它。
- 修正了一条长期误判：此前记为"沙箱出不了外网、push 只能用户自己做"，实测**沙箱可以 push**，前提是走对代理（`7890` 而非 `54437`）。
