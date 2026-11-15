---
name: xhs-profile-notes
description: 抓取并归档小红书某个作者主页的全部笔记（标题、正文、标签、图片、互动数），按页面 SSR 路径工作，不伪造 x-s 签名、不调用 /api/sns/web 接口。用于「抓一下这个博主的所有笔记」「备份小红书作者内容」「小红书主页爬取/导出」，或需要从小红书笔记里提取架构图/截图文字（配 OCR）。触发词：小红书主页、小红书作者、小红书笔记导出、XHS profile scrape、xsec_token、noteDetailMap。
license: MIT
compatibility: 需要 python3、playwright（可用系统 Chrome）、curl_cffi；OCR 需要 tesseract（chi_sim+eng）。需一次人工扫码登录。
metadata:
  author: StockTradingAI local research
  version: "1.0.0"
  short-description: Xiaohongshu author-profile crawler via page SSR
---

# 小红书作者主页笔记抓取

用真实浏览器登录一次，然后走**页面 SSR**（`__INITIAL_STATE__` / `noteDetailMap`）取数据。
不逆 `x-s`、不碰 `/api/sns/web/*`——这两条路实测要么被 `461`/`-104` 拦，要么在登录态下触发 `300011 账号异常`。

## 何时用

- 「把某个小红书博主的笔记都抓下来 / 导出 / 备份」
- 「这个作者最近发了什么，拉个清单」
- 「小红书笔记里的架构图/截图文字提取出来」（配 `scripts/ocr_notes.py`）

不适用：批量关键词搜索、评论抓取、按话题发现内容——那些用 MediaCrawler 之类的多平台框架更省事。

## 硬约束（先看）

1. **必须登录**：未登录访问主页时，站点返回的卡片里 `noteId` 是空串（只有封面和标题），拿不到笔记。
2. **每条笔记要自己的 token**：`/explore/{note_id}` 必须带 `xsec_token`，用主页那个 token 会 404。
3. **别自签 API**：页面里 `window._webmsxyw` 能算出签名，但登录态下用它调 `user_posted/feed/search` 会直接 `code:300011`。
4. **节奏要像人**：串行、4–8s 抖动、每 5 条停 20s；不并发、不重试风暴。
5. **登录态是敏感件**：`login_state.json`、`note_token_pairs.json` 不外发、不提交。

## 工作流

### 1. 准备

```bash
pip install playwright curl_cffi
python -c "from playwright.sync_api import sync_playwright"   # 能 import 即可
# 首次若用自带 chromium：python -m playwright install chromium
# OCR 另需 tesseract（Windows 常见路径 D:\...\Tesseract-OCR\tesseract.exe）
```

### 2. 登录（一次）

```bash
python scripts/fetch_notes.py login --out ./xhs_out
```

会弹真实 Chrome（`channel="chrome"`，独立 profile 在 `<out>/.state/pw_profile`），停在扫码页，最长等 30 分钟；
登录成功后写 `<out>/.state/login_state.json`。**看不到窗口**时让用户确认 Chrome 是否被最小化/在别的桌面。

### 3. 抓取

```bash
python scripts/fetch_notes.py fetch \
  --profile-url "https://www.xiaohongshu.com/user/profile/<USER_ID>?xsec_token=<TOKEN>&xsec_source=app_share" \
  --out ./xhs_out
```

- `xsec_token` 必须来自**主页分享链接**（app 分享出来的那个），不要自己拼。
- 产出：`<out>/notes_all.json`、`<out>/notes_digest.md`、`<out>/.state/images/<note_id>/*`。
- 断点续跑：已存在的图片会跳过；笔记 JSON 每次整体重写，重复跑不会翻倍请求（但仍按节奏）。

### 4. OCR（图片里是图表时）

```bash
python scripts/ocr_notes.py --out ./xhs_out --lang chi_sim+eng --psm 6
# 输出 <out>/ocr/<note_id>_<idx>.txt
```

### 5. 交付

- 先给 `notes_digest.md` 的目录级摘要（标题+日期+互动数+正文前 80 字），再给全文路径。
- 图片 OCR 只作补充：架构图/公式图/面板截图值得 OCR，纯照片不值得。
- 把「事实」与「判断」分开写；引用笔记原文时保留原始错别字与 `[话题]` 标记。

## 失败速查

| 现象 | 原因 / 处理 |
|---|---|
| 主页 302 到 `/login` | 无登录态：先跑 `login` |
| 卡片只有标题、`noteId` 为空 | 未登录或登录态过期（站点降级返回） |
| `/explore/{id}` 404 | 少了 `xsec_token`，或用了主页 token 而不是该笔记的 token |
| `461` + `{"code":0,"success":true,"data":{}}` | 游客访问 API 被风控；改走页面 SSR |
| `-104 当前登录的账号没有权限访问` | 游客/无权限账号；需真实登录 |
| `code:300011 当前账号存在异常` | 自签 API 触发风控；停用 API 路径，等一段时间再走页面 |
| `scroll_into_view_if_needed` 超时 | 虚拟列表；别用 `page.click(selector)` 前的手动滚动，直接点卡片 |
| `dispatchEvent('click')` 跳到 404 | Vue 路由靠组件内 token，不用 href；必须真实点击 |
| `json.loads` 失败（`Expecting value`） | 整页 state 含 `new Map([])` 等 JS 字面量；只切 `"noteDetailMap":` 子树解析 |
| 图片 403 | 缺 referer；带 `Referer: https://www.xiaohongshu.com/` 与登录 cookie |
| OCR 中文乱码 | 缺 `chi_sim`；`tesseract --list-langs` 确认 |

## 参考

- `references/ssr-structure.md`：`__INITIAL_STATE__` 与 `noteDetailMap` 的字段结构、切片解析代码。
- `references/pitfalls.md`：本次实测的完整踩坑记录（含响应码原文、失败路径清单、风控节奏参数）。
