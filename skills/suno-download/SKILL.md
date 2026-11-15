---
name: suno-download
description: 把 Suno（suno.com）公开分享链接的歌曲下载成本地音频。链路是解析 clip id → /api/clip 取元数据 → /api/mango/rights 拿授权块 → SHA-256(guest token) 解包 AES-GCM 包装的 key/iv → AES-CTR 解密加密流 → ffmpeg 出 320k MP3（内嵌封面+歌词标签）、原始 m4a、封面 jpg、lrc。用于「帮我下载这个 Suno 歌曲」「suno 分享链接导出 mp3」「把这个歌存下来」「Suno download / save suno song」，以及排查 suno 下载拿到乱码、403、`moov atom not found`、`audio_url=/api/forbidden`。触发词：suno 下载、suno.com/s/、suno song id、mango rights、suno AES-CTR、suno mp3 导出。
license: MIT
compatibility: python3 + requests + cryptography（另需 ffmpeg/ffprobe、curl 在 PATH）；能访问 suno.com 与 studio-api.prod.suno.com，直连不通时需本机 HTTP 代理（脚本会自动探测）。
metadata:
  author: StockTradingAI local research
  version: "1.0.0"
  short-description: Suno 分享链接下载器（加密流解密 → MP3）
---

# Suno 歌曲下载

一条命令搞定，不必重新逆向。匿名（未登录）即可下载**公开分享**的歌曲。

```bash
python scripts/suno_get.py "https://suno.com/s/dhRRYx9Ce7kdV0dQ" --out "D:\Music"
# 直连失败会自动扫本机 7897/7890/10809/… 端口找代理；也可 --proxy http://127.0.0.1:7897
```

产物（同名一组，中文标题正常写盘）：

| 文件 | 规格 |
|---|---|
| `<标题> - <作者>.mp3` | 320 kbps / 48 kHz 立体声，内嵌 1024×1024 封面 + title/artist/lyrics 标签 |
| `<标题> - <作者>.m4a` | 解密后的原始 mp4 容器（Opus 48 kHz 立体声，未转码） |
| `<标题> - <作者>.jpg` | 官方封面 |
| `<标题> - <作者>.lrc` | 歌词，保留 `[Verse]/[Chorus]` 段落标记 |

## 何时用

- 「帮我下载这个 suno 歌」+ 任意 `suno.com/s/<短码>` 或 `suno.com/song/<uuid>` 链接
- 「suno 上这首歌导成 mp3 / 存到本地 / 发我文件」
- 之前拿到的是乱码 m4a、`403 Forbidden`、`moov atom not found`、`audio_url: /api/forbidden` —— 都是没解密

不适用：私有/未分享作品（`is_public=false` 且无分享 token）、需要 stem 分轨导出、歌单整包下载（循环传多个 id 即可，别开大并发）。

## 关键事实（别再从零踩坑）

1. **`audio_url` 对匿名请求一律返回 `https://studio-api.prod.suno.com/api/forbidden`**，不是 bug，是权限占位。真媒体在 `media_urls[0].url`（CloudFront），`content_type: m4a-opus`、`delivery: progressive`、`encoding: "1.0.0"`。
2. **`cdn1.suno.ai/<id>.mp3` 老路径已 403**（S3 AccessDenied）。别再猜 CDN 直链。
3. **`media_urls` 的字节是 AES-CTR 密文**：直接播会 `moov atom not found`（没有 `ftyp`）。解密后才 `ftyp = isom`。
4. **授权在 `POST /api/mango/rights`**，body `{"content_params":{"content_id":"<uuid>","content_type":"clip"}}`，返回 `{key, iv, glt}`（`content_type` 是枚举，只接受 `clip`；传 `song` 会 422 并列出全部合法枚举）。
5. 解密公式（照抄即可）：
   ```
   gcm_key  = SHA-256(utf8(glt))                    # AES-256-GCM 密钥
   raw_key  = AESGCM(gcm_key).decrypt(iv=blob[:12], ct=blob[12:], aad=uuid)   # key 与 iv 同一处理
   kctr     = raw_key(key)         # 16 字节
   ctr_iv   = raw_key(iv)          # 16 字节初始 counter
   plain    = AES-CTR(key=kctr, counter=ctr_iv, counter_len=128).decrypt(整个文件)
   ```
6. **`yt-dlp` 不可用**：它的 Suno extractor 被硬封，报 `[Liability] This website is not supported`。别浪费时间去升级/打补丁。
7. **网络层已做多路由**：脚本先探测 `studio-api.prod.suno.com` **和** `suno.com` 两个主机，任何一个不通就换下一条候选路由（直连 / 7897 / 7890 / 10809 / …），单个请求失败也会自动切换并记住成功的那条；同名输出自动加 `(2)` 后缀，批量不会互相覆盖。

## 输出校验

```bash
ffprobe -v error -show_entries format=duration,bit_rate:stream=codec_name,channels -of json "X.mp3"
ffmpeg -v error -i "X.mp3" -f null -                 # 无输出 = 解码全程干净
ffmpeg -hide_banner -i "X.mp3" -af volumedetect -f null -   # mean≈-16 dB、max≈0 dB 说明有真实音频
```
脚本已内置这三步，日志里会打 `duration=` 和 `mean_volume/max_volume`。

## 坑位表

| 症状 | 原因 | 处理 |
|---|---|---|
| `moov atom not found` / 十六进制不是 `000000..66747970` | 下的是密文 | 走 `/api/mango/rights` 解密，`raw[4:8]=="ftyp"` 才算成功 |
| `cdn1/cdn2.suno.ai/...mp3` → 403 | 旧直链已废 | 用 `media_urls[0].url` |
| `schannel: failed to receive handshake`（curl） | 本机代理/DNS fake-ip 只认特定 SNI 路由 | 加 `-x http://127.0.0.1:7897`，或 `--ssl-no-revoke`；脚本已内置 curl 回退 |
| `requests` 拉封面时报 `SSL: UNEXPECTED_EOF` | cdn2 的 TLS 在部分代理下握手异常 | 脚本回退到 `curl --ssl-no-revoke`，封面照常保存 |
| `[Liability]` from yt-dlp | extractor 被封 | 别用 yt-dlp |
| m4a 装不进标签：`Could not find tag for codec opus in stream #0` | mp4/ipod muxer 不支持 opus 标签 | 原始 opus 直接整文件写出（不重封装），标签打到 mp3 上 |
| curl 回退时 0 字节 / 报连不上某个死端口 | `curl.exe` 在 Windows 按 ANSI 代码页解释参数，含中文的输出路径写不进去；候选代理列表里还混着没开的端口 | 脚本已改成先写同目录 ASCII 临时名再 `os.replace`，错误信息里会列出每次尝试的真实原因 |
| 无效短码反而下到一首《Page Not Found》 | `/s/<坏码>` 会 302 回首页，页面正文里带着别的 uuid | 只认落地 URL 里的 id，并校验 `?sh=` 与短码一致，不一致直接退出 |
| 中文名在 PowerShell 里显示 `������` | 控制台代码页，不是文件坏了 | 用 `[Console]::OutputEncoding` 或直接 `read` 文件确认 |

## 变体

```bash
python scripts/suno_get.py <id> --no-mp3                  # 只留原始 m4a + 封面 + lrc
python scripts/suno_get.py <id> --wav                    # 追加 48k/24bit WAV
python scripts/suno_get.py <id> --keep-enc               # 保留密文，排查用
python scripts/suno_get.py <id> --curl-only              # 媒体只用 curl（排查 requests/TLS）
python scripts/suno_get.py <id1> <id2> <id3> --jobs 2    # 批量（并发别超 2，会限流）
SUNO_PROXY=http://127.0.0.1:7897 python scripts/suno_get.py <url>   # 环境变量指定代理
```

## 协议细节

端点、请求体、JS 侧证据（哪几个 chunk 里能看到 `mango` / `importKey("AES-CTR")`）、以及**协议变更后如何重新定位**，见 `references/protocol.md`。协议一旦改动，先看那份，按里面的“重定位步骤”抓新 chunk，不要盲猜。
