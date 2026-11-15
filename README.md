# my-skills-repo — 自建 DSH Skill 仓库

存放我自己的 DeepSeek Harness（DSH）skill 的版本库。

## 仓库位置

| 项 | 值 |
| --- | --- |
| 工作副本 | `G:\AIProject\my-skills-repo`（刻意不放 C 盘 `~/.dsh`） |
| 远程（私有） | <https://github.com/liujianjie/my-skills-repo> |
| 分支 | `main`（跟踪 `origin/main`） |

> 注意：仓库本身在 G 盘，但 **skill 生效位置仍是 `%USERPROFILE%\.dsh\skills\`**（C 盘）——DSH 只从那里加载 skill，所以改完要 `-Direction Push` 装回去。仓库只是版本副本与备份，不参与运行。

## 为什么单独建库

机器上原有的 `~/.dsh/agent-skills-repo` 是上游 `addyosmani/agent-skills` 的 clone：

- `origin` 指向第三方仓库，没有写权限，本地 main 已 ahead 197（那是上游新提交，不是我的改动）；
- 收录的是通用软件工程流程类 skill（code-review、TDD、git workflow…），主题上不覆盖「对具体平台干活」的落地型 skill。

GitHub 上 `liujianjie/agent-skills` 只是上游的 fork（public），同理不收自建内容。

所以自建 skill 单独放这里，按主题聚合，不和上游 clone / fork 混在一起。

## 收录范围

「对具体平台/站点执行动作」的落地型 skill：抓取、下载、导出、归档、批处理，以及为它们服务的逆向结论与坑位记录。

通用方法论类 skill 仍留在上游 `agent-skills-repo`，不重复收录。

## 现有 skill

| skill | 主题 | 关键依赖 | 入口 |
| --- | --- | --- | --- |
| [`suno-download`](skills/suno-download/SKILL.md) | Suno 公开分享链接 → 本地音频：`/api/clip` 元数据 → `/api/mango/rights` 授权块 → SHA-256(guest token) 解 AES-GCM 包 key/iv → AES-CTR 解密 → ffmpeg 出 320k MP3（含封面/歌词标签）、m4a、封面 jpg、lrc | python3 + requests + cryptography；ffmpeg/ffprobe、curl 在 PATH | [`scripts/suno_get.py`](skills/suno-download/scripts/suno_get.py) |
| [`xhs-profile-notes`](skills/xhs-profile-notes/SKILL.md) | 小红书作者主页全部笔记归档（标题/正文/标签/图片/互动数），走页面 SSR（`__INITIAL_STATE__`、`noteDetailMap`），不伪造 x-s、不调 `/api/sns/web`；附截图 OCR | python3 + playwright + curl_cffi；OCR 需 tesseract(chi_sim+eng) | [`scripts/fetch_notes.py`](skills/xhs-profile-notes/scripts/fetch_notes.py) |

两者配套的 `references/` 是从实际调试里沉淀的协议/坑位记录（`protocol.md`、`ssr-structure.md`、`pitfalls.md`），改脚本前先读。

## 目录结构

```
my-skills-repo/
├─ skills/
│  └─ <skill-name>/
│     ├─ SKILL.md          # frontmatter: name/description/license/compatibility/metadata
│     ├─ references/       # 协议、页面结构、坑位记录
│     └─ scripts/          # 可执行脚本
├─ scripts/
│  └─ sync-from-dsh.ps1    # 与 ~/.dsh/skills 双向同步
└─ README.md
```

## 安装到 DSH

DSH 读取的 skill 目录是 `%USERPROFILE%\.dsh\skills\`（本机 `C:\Users\Administer\.dsh\skills`）。两种方式：

```powershell
# 方式一：仓库 → DSH（新机器/恢复时）
pwsh -File .\scripts\sync-from-dsh.ps1 -Direction Push

# 方式二：手工复制
Copy-Item -Recurse -Force .\skills\suno-download "$env:USERPROFILE\.dsh\skills\"
```

反向（DSH 里改完，收进仓库）用 `-Direction Pull`。

## 敏感与产物：不入库

`.gitignore` 已排除：登录态（`login_state.json` 含 cookie）、`note_token_pairs.json`、抓取产物（`notes_all.json`、`notes_digest.md`）、下载音视频与图片、`__pycache__`。

提交前自检：

```powershell
git status --short   # 不应出现 .state/、*.json 产物、*.mp3/*.m4a/*.jpg
git diff --cached --stat
```

## 新增 skill 的流程

1. 在 `%USERPROFILE%\.dsh\skills\<new-skill>` 里开发和实跑；
2. `pwsh -File .\scripts\sync-from-dsh.ps1 -Direction Pull -Name <new-skill>`；
3. 更新本 README 的 skill 表与主题说明；
4. `git add -A && git commit -m "feat(<new-skill>): <一句话>"`。

## License

各 skill 目录内 `SKILL.md` 标注 `license: MIT`，仓库整体同 MIT。
