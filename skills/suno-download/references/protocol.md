# Suno 下载协议备忘（2026-11 实测）

## 1. 数据流

```
GET  suno.com/s/<short>                     302 -> /song/<uuid>?sh=<short>
GET  studio-api.prod.suno.com/api/clip/<uuid>/   元数据 + media_urls + image_large_url
GET  <media_urls[0].url>                    CloudFront，AES-CTR 密文（.m4a，opus）
POST studio-api.prod.suno.com/api/mango/rights    {"content_params":{"content_id","content_type":"clip"}}
                                            -> {"key":"<b64>","iv":"<b64>","glt":"<token>"}
     AES-GCM 解包 key/iv  ->  AES-CTR 解密媒体  ->  合法 mp4(ftyp=isom)
```

匿名即可完成全部四步。`/api/clip/<uuid>/` 与 `/api/mango/rights` 都不需要登录 cookie；
页面访问会顺带发 `statsig_stable_id` / `ajs_anonymous_id` 两个 cookie，实测非必需。

## 2. 端点与字段

### GET `/api/clip/<uuid>/`

关键字段：

| 字段 | 说明 |
|---|---|
| `audio_url` | 匿名请求恒为 `https://studio-api.prod.suno.com/api/forbidden`（占位符，别当直链） |
| `media_urls[]` | `{url, content_type:"m4a-opus", delivery:"progressive", encoding:"1.0.0"}`；`encoding` 有值 = 加密 |
| `metadata.prompt` | 歌词，含 `[Intro]/[Verse]/[Chorus]` 标记 |
| `metadata.tags` | 生成用的风格提示词（换行分隔） |
| `metadata.duration` | 秒，浮点 |
| `major_model_version` / `model_name` | 如 `v6-mini` / `chirp-goose` |
| `image_large_url` | 1024×1024 封面（`cdn2.suno.ai`） |
| `is_public` | 分享出来的作品可能仍是 `false`（unlisted），不影响匿名下载 |

### POST `/api/mango/rights`

```json
{"content_params": {"content_id": "<uuid>", "content_type": "clip"}}
```

`content_type` 是严格枚举（`clip`/`flow`/`hook`/`playlist`/`album`/`persona`… ），
传 `song` 会 422 并把全部合法值列出来。

响应：

```json
{"key": "<base64>", "iv": "<base64>", "glt": "<guest token 字符串>"}
```

`key`、`iv` 都是 **12 字节 IV + GCM 密文** 的 base64 串。

## 3. 解密算子（JS 原文语义）

客户端实现落在 `/_next/static/immutable/chunks/` 的加密媒体模块里（模块 id `913328`、`282966`，调用方 `930323`）。
压缩后的导出名与语义：

| 导出 | 语义 |
|---|---|
| `g` | `deriveKey(str)` = `SHA-256(utf8(str))` → `importKey("raw", digest, "AES-GCM", ["decrypt"])` |
| `w` | `resolveKey(gltKey, guestToken)`：有现成 key 就用，否则用 guest token 派生；都没有则抛 `License server response is missing the guest token for anonymous playback` |
| `E` | `gcmDecrypt(blob, aad, key)`：`iv = blob.slice(0,12)`，`additionalData = utf8(aad)`，密文 `blob.slice(12)` |
| `J` | `importKey("raw", 解包结果, "AES-CTR", ["decrypt"])` |
| `b` | `addCounter(iv16, blockOffset)`：把 16 字节 IV 当大整数加 `offset`（按 16 字节块计） |
| `T` | 流式解密：`counter = addCounter(iv, i/16)`、`length: 128`、按 16 字节对齐分块 |

等价 Python（已验证）：

```python
gcm = AESGCM(hashlib.sha256(glt.encode()).digest())
raw_key = gcm.decrypt(blob[:12], blob[12:], uuid.encode())   # key / iv 各自如此
plain = AES-CTR(key=raw_key, counter=raw_iv, counter_len=128).decrypt(整个文件)
```

判定成功：`plain[4:8] == b"ftyp"`，随后 `ffprobe` 应报 `codec_name=opus, 48000 Hz, 2ch`。

## 4. 协议变了怎么重新定位（不要盲猜）

1. 取页面并解出落地地址（短链会 302 到 `/song/<uuid>?sh=...`）：
   ```bash
   curl -sSL -x http://127.0.0.1:7897 -A "Mozilla/5.0" "https://suno.com/s/<short>" -o page.html
   grep -o 'song/[0-9a-f-]\{36\}' page.html | head
   ```
2. 页面是 Next.js App Router，歌曲数据在 flight 流里（`self.__next_f.push([1,"...json..."])`）：
   ```python
   import re, json
   html = open("page.html", encoding="utf-8", errors="replace").read()
   data = "".join(json.loads(c) for c in re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html))
   ```
   在 `data` 里搜 `media_urls`、`"encoding"`、`"audio_url"`。
3. 拉全部 JS chunk 再搜关键字，比读 8MB 压缩包靠猜快得多：
   ```bash
   grep -o '/_next/static/[^"\\]*\.js' page.html | sort -u > chunks.txt
   # 逐个下载后：
   grep -l -i "mango\|subtle.decrypt\|AES-CTR\|guest token" _js/*.js
   ```
   命中 `guest token` / `License server response` 的那个 chunk 就是授权+解密模块。
4. 对新端点抓一次响应结构：
   ```bash
   curl -sS -x http://127.0.0.1:7897 -H "Content-Type: application/json" \
     -d '{"content_params":{"content_id":"<uuid>","content_type":"clip"}}' \
     https://studio-api.prod.suno.com/api/mango/rights
   ```
   若报 422，错误体里会列出新的枚举值。
5. 用 `encoding` 字段判断是否需要解密：没有 `encoding` 就是明文，直接保存即可。

## 5. 实测样本（回归基准）

| 项 | 值 |
|---|---|
| clip | `247a7270-7b5f-407f-a29d-521f4d77909c`（报告妈 / liujianjie / v6-mini） |
| 密文大小 | 1 766 836 B（约 133 kbps opus，105.96 s） |
| 解包 key / iv | 均 16 B；本次 counter `42c4e33256c76560f665ab5a7acddcbd` |
| 解密后 | `ftyp=isom`，`opus 48000 Hz 2ch`，时长 105.96 s |
| mp3 | 320 kbps / 48 kHz，约 4.29 MB，`mean_volume -16.2 dB / max 0.0 dB` |

注意：`key`/`iv`/`glt` 是**按请求签发**的，样本里的 counter 只用于当时那一次响应，别硬编码进脚本。

## 6. 网络侧事实

- `studio-api.prod.suno.com` 的 DNS 在本机会被解析到 `198.18.x.x`（Clash fake-ip）；直连 TLS 常被重置，走本机 HTTP 代理（实测 `127.0.0.1:7897`）稳定。
- `cdn2.suno.ai` 图片在部分代理下会 `SSL: UNEXPECTED_EOF_WHILE_READING`；`curl --ssl-no-revoke` 可过。
- `yt-dlp` 的 Suno extractor 已被上游移除并硬报 `[Liability] This website is not supported`，不要依赖。
- 路由探测用 `GET /api/oembed?url=...`（返回 400/404 都算连通），比拿假 uuid 打 `/api/clip/<zeros>/` 稳，后者偶发 5xx 会把探测带偏。

## 7. 两个非显然的坑（都踩过）

### 7.1 `curl.exe` 写不进中文路径

Windows 自带 `curl.exe` 以 ANSI 代码页解析参数，`-o "D:\...\报告妈 - liujianjie.enc.bin"` 会失败或落到乱码文件名，Python 侧 `os.path.exists(原路径)` 为假，于是“下载失败”。

解决：目标路径含非 ASCII 时先写同目录的 `_dl_<md5前10位>.part`，成功后 `os.replace` 改名。

```python
tmp = dst if dst.isascii() else os.path.join(os.path.dirname(dst), "_dl_%s.part" % hashlib.md5(dst.encode()).hexdigest()[:10])
```

另外报错要保留**每次**尝试的原因，别只留最后一条——最后的候选往往是没开的端口，
`Failed to connect to 127.0.0.1 port 33210` 会把人带偏。

### 7.2 `/s/<坏短码>` 会返回一首真实的《Page Not Found》

无效分享码 302 回 `https://suno.com/`，而站点首页/Hydration 数据里含别的 clip uuid；
若从 HTML 正文用正则抓第一个 uuid，就会安静地下错歌（实测抓到 `226e9571-…` / `dweb-suno` / v6）。
**只从落地 URL 判定 id**，且校验 `?sh=` 与用户给的短码一致，不一致直接报错退出。

### 7.3 路由要按“两个主机”判活

`studio-api.prod.suno.com` 与 `suno.com` 的连通性会分别抖动（实测 direct 下 API 通、站点 SSL EOF）。
只探一个主机会出现「路由判定为直连，随后 `resolve_id` 直接崩」。
做法：探测 `GET https://studio-api.prod.suno.com/api/oembed?url=...` + `GET https://suno.com/`，
两者都通才算可用；请求层面再加一层「失败即切下一条候选路由」的兜底。
另外批量下载同一首歌（或两首歌标题撞了）会互相覆盖同名文件，用一把锁给 base 名加 `(2)(3)`。
