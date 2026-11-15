#!/usr/bin/env python3
"""Suno 单曲下载器 —— 支持 suno.com 公开分享链接（未登录 / 匿名播放）。

用法:
    python suno_get.py https://suno.com/s/dhRRYx9Ce7kdV0dQ
    python suno_get.py <share-url|song-url|clip-id> [...] --out D:\\Music
    python suno_get.py <url> --no-mp3            # 只要原始 m4a(opus) + 封面 + 歌词
    python suno_get.py <url> --wav              # 额外导出 48k WAV
    python suno_get.py <url> --proxy http://127.0.0.1:7897
    python suno_get.py <url> --keep-enc         # 保留加密原始字节用于排查

输出（同名一组）:
    <标题> - <作者>.mp3   320kbps 48kHz，内嵌封面 + 标题/作者/歌词标签
    <标题> - <作者>.m4a   解密后的原始 mp4 容器（Opus 48kHz 立体声，未转码）
    <标题> - <作者>.jpg   1024x1024 封面
    <标题> - <作者>.lrc   歌词（带 [Verse]/[Chorus] 段落标记）

依赖: requests, cryptography, ffmpeg/ffprobe 在 PATH 中。
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
API = "https://studio-api.prod.suno.com"
PROBE = API + "/api/oembed?url=https%3A%2F%2Fsuno.com%2Fsong%2F"
CLIP_ID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# 本机常见代理端口，直连失败时逐个探测
PROXY_PORTS = [7897, 7890, 7891, 10809, 10808, 1080, 2080, 8889, 6152, 33210]


def log(msg: str) -> None:
    print(msg, flush=True)


def new_session(proxy: str | None) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Referer": "https://suno.com/",
        "Origin": "https://suno.com",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    return s


def first_file(url: str, dst: str, sess: requests.Session | None = None,
               referer: str | None = None, try_direct_first: bool = False) -> str:
    """下载到 dst；requests 失败时回退 curl（解决 schannel/cdn SSL EOF 等怪问题）。

    curl.exe 在 Windows 上按 ANSI 代码页解释参数，中文路径会写失败，
    因此路径含非 ASCII 字符时先落盘到同目录的 ASCII 临时名，再用 os.replace 改名。
    """
    last = None
    errs: list[str] = []
    if sess is not None:
        try:
            with sess.get(url, stream=True, timeout=180) as r:
                r.raise_for_status()
                with open(dst, "wb") as f:
                    for ch in r.iter_content(262144):
                        f.write(ch)
            if os.path.getsize(dst) > 0:
                return dst
        except Exception as e:  # noqa: BLE001
            last = e
            errs.append("requests: %s" % str(e)[:120])
    ref = referer or "https://suno.com/"
    tmp = dst
    if not dst.isascii():
        tmp = os.path.join(os.path.dirname(dst) or ".",
                           "_dl_%s.part" % hashlib.md5(dst.encode("utf-8")).hexdigest()[:10])
    order: list[str | None] = []
    if sess is not None and sess.proxies:
        order.append(sess.proxies.get("https") or sess.proxies.get("http"))
    if try_direct_first:
        order.insert(0, None)
    order.append(None)                     # 直连
    order += [p for p in _candidate_proxies(sess) if p not in order]
    for proxy in order:
        cmd = ["curl", "-sSL", "--ssl-no-revoke", "--max-time", "300",
               "-A", UA, "-H", "Referer: %s" % ref, "-o", tmp, url]
        if proxy:
            cmd[1:1] = ["-x", proxy]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if p.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            if tmp != dst:
                os.replace(tmp, dst)
            return dst
        last = p.stderr.strip() or last
        errs.append("curl%s: %s" % ("[%s]" % (proxy or "direct"),
                                    (p.stderr or "").strip().replace("\n", " ")[:120]))
    raise RuntimeError("download failed: %s | %s" % (url, " ; ".join(errs[:4]) or last))


_CANDIDATES: list[str] = []


def _candidate_proxies(sess) -> list[str]:
    if _CANDIDATES:
        return _CANDIDATES
    if sess is not None and sess.proxies:
        p = sess.proxies.get("https") or sess.proxies.get("http")
        if p:
            _CANDIDATES.append(p)
    for port in PROXY_PORTS:
        url = "http://127.0.0.1:%d" % port
        if url not in _CANDIDATES:
            _CANDIDATES.append(url)
    return _CANDIDATES


def _reachable(s: requests.Session, timeout: int = 12) -> bool:
    """两个主机都要能通：API 和站点本身（二者路由可能不同）。"""
    for u in (PROBE, "https://suno.com/"):
        try:
            if s.get(u, timeout=timeout).status_code >= 500:
                return False
        except requests.RequestException:
            return False
    return True


def candidates(proxy: str | None) -> list[tuple[str, requests.Session]]:
    if proxy:
        return [("proxy %s (指定)" % proxy, new_session(proxy))]
    out = [("direct", new_session(None))]
    for port in PROXY_PORTS:
        p = "http://127.0.0.1:%d" % port
        out.append(("proxy %s" % p, new_session(p)))
    return out


def pick_route(proxy: str | None) -> dict:
    """选第一条两主机都能通的路由；都不通则留直连，交给 fetch 在请求时逐个再试。"""
    cands = candidates(proxy)
    for label, s in cands:
        if _reachable(s):
            log("route: %s" % label)
            return {"s": s, "label": label, "cands": cands}
    log("route: direct (探测全部失败，请求时逐个重试)")
    return {"s": cands[0][1], "label": cands[0][0], "cands": cands}


def fetch(box: dict, url: str, method: str = "GET", **kw) -> requests.Response:
    """带路由切换的请求：失败就换下一条候选路由，成功即记住。"""
    order = [box["s"]] + [s for _, s in box["cands"] if s is not box["s"]]
    labels = {id(s): lab for lab, s in box["cands"]}
    last: Exception | None = None
    kw.setdefault("timeout", 45)
    for s in order:
        try:
            r = s.request(method, url, **kw)
            r.raise_for_status()
            box["s"] = s
            return r
        except requests.HTTPError as e:
            raise e                       # 4xx/5xx 是业务错误，换路由没意义
        except requests.RequestException as e:
            last = e
            log("  路由 %s 失败(%s)，换下一条" % (labels.get(id(s), "?"), type(e).__name__))
    raise last if last else RuntimeError("all routes failed")


def fetch_json(box: dict, url: str, method: str = "GET", **kw) -> dict:
    return fetch(box, url, method=method, **kw).json()


def resolve_id(box: dict, arg: str) -> str:
    m = re.search(r"/song/(%s)" % CLIP_ID_RE, arg)
    if m:
        return m.group(1)
    if re.fullmatch(CLIP_ID_RE, arg):
        return arg
    r = fetch(box, arg, allow_redirects=True)
    # 只认落地 URL，绝不从页面正文里猜 id：无效短码会落到一个
    # 标题叫 "Page Not Found" 的真实曲目，正文里带别的 uuid，猜了就下错歌。
    m = re.search(r"/song/(%s)" % CLIP_ID_RE, r.url)
    if not m:
        raise SystemExit("解析不到 clip id（链接失效或不是分享页）: %s -> %s" % (arg, r.url))
    short = re.search(r"/s/([A-Za-z0-9_-]+)", arg)
    if short:
        got = re.search(r"[?&]sh=([A-Za-z0-9_-]+)", r.url)
        if got and got.group(1) != short.group(1):
            raise SystemExit("短码与落地地址不一致，疑似失效: /s/%s -> sh=%s"
                             % (short.group(1), got.group(1)))
    return m.group(1)


def b64d(txt: str) -> bytes:
    t = txt.strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(t + "=" * (-len(t) % 4))


def unwrap(blob_b64: str, glt: str, cid: str) -> bytes:
    """AES-GCM 解包：SHA-256(guest token) 作密钥，AAD = content_id，前 12 字节是 IV。"""
    aead = AESGCM(hashlib.sha256(glt.encode("utf-8")).digest())
    raw = b64d(blob_b64)
    return aead.decrypt(raw[:12], raw[12:], cid.encode("utf-8"))


def pick_media(meta: dict) -> tuple[str, bool]:
    """返回 (url, encrypted)。优先非加密 mp3，其次 progressive 媒体。"""
    url = meta.get("audio_url") or ""
    if url and "/api/forbidden" not in url:
        return url, False
    media = meta.get("media_urls") or []
    media = [m for m in media if m.get("url")]
    media.sort(key=lambda m: (
        0 if "mp3" in (m.get("content_type") or "") else 1,
        0 if m.get("delivery") == "progressive" else 1))
    if not media:
        raise SystemExit("no media_urls / audio_url for this clip")
    best = media[0]
    return best["url"], bool(best.get("encoding"))


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .")


_CLAIMED: set[str] = set()
_CLAIM_LOCK = threading.Lock()


def claim_base(base: str) -> str:
    """同名输出（同一首歌重复给、或不同歌撞了标题）自动加 (2)(3) 后缀，避免并发互覆盖。"""
    with _CLAIM_LOCK:
        cand, n = base, 1
        while cand in _CLAIMED:
            n += 1
            cand = "%s (%d)" % (base, n)
        _CLAIMED.add(cand)
        return cand


def ffprobe_json(path: str, args: str) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", args,
                        "-of", "json", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def volume_stats(path: str) -> str:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    got = [ln.strip().split("] ", 1)[-1] for ln in p.stderr.splitlines() if "volume:" in ln]
    return " | ".join(got)


def build_outputs(src: str, cover: str | None, base: str, meta: dict,
                  want_mp3: bool, want_wav: bool) -> list[str]:
    md = meta.get("metadata") or {}
    title = meta.get("title") or "suno"
    author = meta.get("display_name") or meta.get("handle") or "unknown"
    lyrics = md.get("prompt") or ""
    tags = (md.get("tags") or "").replace("\n", " ")
    made = []

    if want_mp3:
        mp3 = base + ".mp3"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src]
        if cover and os.path.exists(cover):
            cmd += ["-i", cover, "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg",
                    "-disposition:v", "attached_pic"]
        else:
            cmd += ["-map", "0:a"]
        cmd += ["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000",
                "-id3v2_version", "3",
                "-metadata", "title=%s" % title,
                "-metadata", "artist=%s" % author,
                "-metadata", "album=Suno",
                "-metadata", "genre=%s" % tags[:60],
                "-metadata", "comment=https://suno.com/song/%s" % meta.get("id", ""),
                "-metadata", "lyrics=%s" % lyrics, mp3]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if p.returncode:
            log("  ! mp3 失败: %s" % p.stderr.strip()[:300])
        else:
            made.append(mp3)

    if want_wav:
        wav = base + ".wav"
        p = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-i", src, "-c:a", "pcm_s24le", "-ar", "48000", wav],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if p.returncode:
            log("  ! wav 失败: %s" % p.stderr.strip()[:300])
        else:
            made.append(wav)
    return made


def handle(target: str, out_dir: str, proxy: str | None, want_mp3: bool,
           want_wav: bool, keep_enc: bool, curl_only: bool = False) -> int:
    if proxy:
        log("route: proxy %s (指定)" % proxy)
        box = pick_route(proxy)
    else:
        box = pick_route(None)
    cid = resolve_id(box, target)

    meta = fetch_json(box, "%s/api/clip/%s/" % (API, cid))
    s = box["s"]
    md = meta.get("metadata") or {}
    title = meta.get("title") or cid
    author = meta.get("display_name") or meta.get("handle") or "unknown"
    base = claim_base(os.path.join(out_dir, safe("%s - %s" % (title, author))))
    os.makedirs(out_dir, exist_ok=True)
    log("[%s] %s | %s | %ss | %s" % (cid[:8], title, author,
                                     md.get("duration"), meta.get("major_model_version")))

    url, encrypted = pick_media(meta)
    enc_path = base + ".enc.bin"
    dl_sess = None if curl_only else s
    first_file(url, enc_path, sess=dl_sess)
    raw = open(enc_path, "rb").read()
    log("  media %d bytes | encrypted=%s" % (len(raw), encrypted))

    if encrypted:
        lic = fetch_json(box, "%s/api/mango/rights" % API, method="POST",
                         data=json.dumps({"content_params": {
                             "content_id": cid, "content_type": "clip"}}))
        s = box["s"]
        key = unwrap(lic["key"], lic["glt"], cid)
        iv = unwrap(lic["iv"], lic["glt"], cid)
        dec = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()
        raw = dec.update(raw) + dec.finalize()
        log("  decrypt ok | key=%dB iv=%s" % (len(key), iv.hex()))
    if raw[4:8] != b"ftyp":
        raise SystemExit("解密结果不是合法 mp4（密钥/counter 不对）")

    m4a = base + ".m4a"
    with open(m4a, "wb") as f:
        f.write(raw)
    with open(base + ".lrc", "w", encoding="utf-8") as f:
        f.write((md.get("prompt") or "").replace("\n\n", "\n") + "\n")

    cover = base + ".jpg"
    img = meta.get("image_large_url") or meta.get("image_url")
    if img:
        try:
            first_file(img, cover, sess=dl_sess, try_direct_first=False)
        except Exception as e:  # noqa: BLE001
            log("  ! 封面失败: %s" % e)
            cover = None

    made = [m4a, base + ".lrc"] + ([cover] if cover else [])
    made += build_outputs(m4a, cover, base, meta, want_mp3, want_wav)

    if not keep_enc:
        os.remove(enc_path)

    info = ffprobe_json(base + ".mp3" if want_mp3 else m4a,
                        "format=duration,bit_rate:stream=codec_name,channels,sample_rate")
    dur = (info.get("format") or {}).get("duration")
    log("  duration=%ss  loud=%s" % (dur, volume_stats(made[0] if made else m4a)))
    for f in made:
        if os.path.exists(f):
            log("  %-58s %9d B" % (os.path.basename(f), os.path.getsize(f)))
    return 0


def _safe_handle(target: str, out_dir: str, proxy: str | None, want_mp3: bool,
                 want_wav: bool, keep_enc: bool, curl_only: bool) -> int:
    try:
        return handle(target, out_dir, proxy, want_mp3, want_wav, keep_enc, curl_only)
    except BaseException as e:  # noqa: BLE001
        log("! %s -> %s" % (target, e))
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Suno 公开分享歌曲下载（含 AES-CTR 流解密）")
    ap.add_argument("targets", nargs="+", help="分享链接 / song 链接 / clip id，可多个")
    ap.add_argument("--out", default=".", help="输出目录，默认当前目录")
    ap.add_argument("--proxy", default=os.environ.get("SUNO_PROXY", ""),
                    help="如 http://127.0.0.1:7897；留空则直连失败后自动探测本机代理")
    ap.add_argument("--no-mp3", action="store_true", help="不转 mp3，只留原始 m4a")
    ap.add_argument("--wav", action="store_true", help="额外导出 48kHz/24bit WAV")
    ap.add_argument("--keep-enc", action="store_true", help="保留加密原始字节")
    ap.add_argument("--curl-only", action="store_true",
                    help="媒体只用 curl 下载（排查 requests 侧 TLS/代理问题时用）")
    ap.add_argument("--jobs", type=int, default=1, help="并发下载数量，默认 1（避免限流）")
    a = ap.parse_args()

    for exe in ("ffmpeg", "ffprobe", "curl"):
        if not shutil.which(exe):
            log("! 缺少 %s，部分步骤会失败" % exe)

    proxy = a.proxy or None
    # 完全相同的目标只跑一次（重复给 id 会撞输出名）
    targets, seen = [], set()
    for t in a.targets:
        key = t.strip().lower()
        if key not in seen:
            seen.add(key)
            targets.append(t)
    rc = 0
    if a.jobs > 1 and len(targets) > 1:
        with futures.ThreadPoolExecutor(a.jobs) as ex:
            list(ex.map(lambda t: _safe_handle(t, a.out, proxy, not a.no_mp3,
                                               a.wav, a.keep_enc, a.curl_only), targets))
    else:
        for t in targets:
            try:
                rc |= handle(t, a.out, proxy, not a.no_mp3, a.wav,
                             a.keep_enc, a.curl_only)
            except Exception as e:  # noqa: BLE001
                log("! %s -> %s" % (t, e))
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
