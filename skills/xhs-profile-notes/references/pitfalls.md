# 小红书主页抓取：实测踩坑记录（2026-11-15）

环境：Windows 11、Chrome 真实安装、Python 3.13、playwright 1.60、curl_cffi 0.15。
目标：抓 `xiaohongshu.com/user/profile/<24hex>` 的作者全部公开笔记（本次 20 篇）。

## 一、一次性结论

- 读接口（`/api/sns/web/*`）**全部要登录态**，游客一律 `461` 或 `-104`。
- 页面 SSR **不需要伪造签名**，带上登录 cookie 与 `xsec_token` 就能拿 HTML。
- 登录态下**自签 API 会被风控**（`300011`），比不签更糟。
- 未登录时主页 SSR 会**清空 `noteId`**，只给封面和标题——看起来「抓到了」，其实拿不到正文。

## 二、按尝试顺序记录的失败路径

| # | 做法 | 结果 | 判读 |
|---|---|---|---|
| 1 | `web_fetch` 主页 URL | 302 → `/login?redirectPath=…` | 无 cookie |
| 2 | `curl_cffi impersonate=chrome` + 真实 UA | 302 登录墙 | TLS 指纹对也没用 |
| 3 | `curl_cffi impersonate=safari` | 200，含 `__INITIAL_STATE__`，20 张卡片，`noteId` 全空 | 站点降级策略 |
| 4 | Playwright 打开主页（未登录） | 客户端路由跳 `/login` | 前端识别 |
| 5 | 页面内 `window._webmsxyw(uri, data)` 自签 + `fetch` `user_posted` | `HTTP 461`，`{"code":0,"success":true,"msg":"成功","data":{}}` | 签名没问题，账号无权限 |
| 6 | 同上，`search/notes` | `HTTP 200`，`{"code":-104,"msg":"当前登录的账号没有权限访问"}` | 游客限制 |
| 7 | 同上，`feed`（用 explore 页拿到的真实 note id） | `461` + 空 data | 同上 |
| 8 | 检查 `_webmsxyw` 用法：`data` 传对象 / `undefined` / 传完整 URL | 只有 `undefined` 形态能过签名（200） | 传参要 `(uri, undefined)` for GET |
| 9 | 登录后再自签调 `feed` | `code:300011 当前账号存在异常，请切换账号或稍后重试` | **风控触发，停用 API 路径** |
| 10 | `curl_cffi` + 登录 cookie 抓主页 | 200，`noteId` 有值（20 对 id/token） | ✅ 可用 |
| 11 | 直接用主页 token 拼 `/explore/{id}` | 全部 302 → `/404?source=/404/sec_…` | token 必须逐条配对 |
| 12 | `a[href*='/explore/']` 的 href | 只有 `/explore/{id}`，**没有 token** | Vue 跳转时才注入 token |
| 13 | `dispatchEvent(new MouseEvent('click'))` | 跳到 `/404` | 合成事件不触发路由的 token 逻辑 |
| 14 | `page.click("section.note-item")`（真实点击） | 成功跳到 `/explore/{id}?xsec_token=…` | ✅ 但要能滚动到元素 |
| 15 | `element.scroll_into_view_if_needed()` | `TimeoutError`（8s） | 虚拟列表，元素不可见/不在视口 |
| 16 | 读 Vue 内部（`__vueParentComponent`）找 token | 拿不到（生产构建/封装） | 放弃 |
| 17 | 主页 SSR 的 `user.notes[0][i].noteCard.xsecToken` | 直接给出每条的 token | ✅ 最省事 |
| 18 | 用登录 cookie + 逐条 `/explore/{id}?xsec_token=…` | 200，HTML 含 `"noteDetailMap"` | ✅ 主路径 |
| 19 | 整页 `json.loads(__INITIAL_STATE__)` | `Expecting value: line 1 column 24557` | 内含 `new Map([])` |
| 20 | 只切 `"noteDetailMap":` 子树再 `json.loads` | 成功，`desc` / `imageList` / `tagList` 齐全 | ✅ |
| 21 | 笔记图片直链下载 | 200 webp | 带 referer + cookie |
| 22 | 搜索「site:xiaohongshu.com …」找笔记 URL | 0 条可用 | 笔记页基本不被索引 |
| 23 | `xhh.club`（以为是小书镜像） | 是 AI 摘要站（按 URL 哈希索引博客/公众号） | 不是小红书镜像 |

## 三、风控参数（本次实测安全区）

- 串行请求；笔记页间隔 4–8s 抖动；每 5 条停 20s；图片 0.8–1.8s。
- 总请求量：约 40 次页面 + 59 张图，抓完 20 篇 **未出现验证码/滑块**。
- 一旦出现 `300011`：立即停 API，只走页面 SSR；等一段时间再试。
- `login_state.json` 会被站点刷新（`web_session` 轮换），长期任务每次跑前复用同一 profile 即可。

## 四、登录窗口的坑

- `headless=True` 的扫码页用户看不见 → 登录必须 `headless=False` + `--start-maximized` + `bring_to_front()`。
- 登录判定别只看 URL：宽限期用 cookie 集合 `{web_session, webId, a1}` 是否齐全来判。
- 首次跑曾 600s 超时退出，用户「没看到窗口」；改成 1800s 并置前。

## 五、脚本与产物对照

| 阶段 | 命令 | 产物 |
|---|---|---|
| 登录 | `fetch_notes.py login` | `.state/login_state.json`、`.state/pw_profile/` |
| 抓主页 | `fetch_notes.py fetch --profile-url …` | `.state/note_token_pairs.json` |
| 抓正文 | 同上（同一轮内） | `notes_all.json`、`notes_digest.md`、`.state/images/` |
| OCR | `ocr_notes.py` | `ocr/<note_id>_<idx>.txt` |
