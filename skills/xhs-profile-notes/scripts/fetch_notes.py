#!/usr/bin/env python3
"""小红书作者主页笔记抓取：登录 + 页面 SSR，不伪造签名、不调 /api/sns/web。

  python fetch_notes.py login  --out ./xhs_out
  python fetch_notes.py fetch  --profile-url "<主页分享链接>" --out ./xhs_out

产物：<out>/notes_all.json、<out>/notes_digest.md、<out>/.state/{login_state.json,images/,note_token_pairs.json}
"""
from __future__ import annotations

import argparse
import datetime
import json
import random
import re
import time
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
KEY = '"noteDetailMap":'


# ------------------------------------------------------------------ parsing
def slice_object(s: str, start: int) -> str | None:
    depth = 0
    instr = esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
            continue
        if ch == '"':
            instr = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def extract_state(html: str) -> dict:
    """主页 __INITIAL_STATE__ 可能是 JS（含 new Map([])），先切片再试解析。"""
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*", html)
    if not m:
        return {}
    raw = slice_object(html, m.end())
    if not raw:
        return {}
    try:
        return json.loads(re.sub(r"\bundefined\b", "null", raw))
    except Exception:
        return {}


def note_from_html(html: str, nid: str) -> dict:
    pos = -1
    while True:
        pos = html.find(KEY, pos + 1)
        if pos < 0:
            return {}
        raw = slice_object(html, pos + len(KEY))
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if nid in obj:
            return (obj[nid] or {}).get("note") or {}


def load_cookies(state_dir: Path) -> dict:
    st = json.loads((state_dir / "login_state.json").read_text(encoding="utf-8"))
    return {c["name"]: c["value"] for c in st.get("cookies", []) if "xiaohongshu" in c.get("domain", "")}


# ------------------------------------------------------------------ login
def cmd_login(out: Path) -> None:
    from playwright.sync_api import sync_playwright

    state = out / ".state"
    state.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(state / "pw_profile"), channel="chrome", headless=False,
            no_viewport=True, locale="zh-CN", timezone_id="Asia/Shanghai",
            args=["--disable-blink-features=AutomationControlled", "--lang=zh-CN",
                  "--start-maximized", "--no-first-run", "--no-default-browser-check"])
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.goto("https://www.xiaohongshu.com/login", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(6000)
        try:
            pg.bring_to_front()
        except Exception:
            pass
        print("[login] 在弹出的 Chrome 里扫码；最长等 30 分钟", flush=True)
        deadline = time.time() + 1800
        while time.time() < deadline:
            names = {c["name"] for c in ctx.cookies() if "xiaohongshu" in c["domain"]}
            if {"web_session", "webId", "a1"} <= names and "login" not in pg.url:
                break
            time.sleep(3)
        ctx.storage_state(path=str(state / "login_state.json"))
        print("[login] saved:", state / "login_state.json", "| url:", pg.url[:80], flush=True)
        ctx.close()


# ------------------------------------------------------------------ fetch
def cmd_fetch(profile_url: str, out: Path, min_delay: float, max_delay: float,
              pause_every: int, pause_s: int) -> None:
    from curl_cffi import requests as creq

    state = out / ".state"
    state.mkdir(parents=True, exist_ok=True)
    if not (state / "login_state.json").exists():
        raise SystemExit("缺少 .state/login_state.json：先跑 `fetch_notes.py login`")
    cookies = load_cookies(state)

    # 1) 主页 SSR → noteId + 每条自己的 xsecToken
    r = creq.get(profile_url, impersonate="chrome", cookies=cookies, timeout=45,
                 headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                          "Referer": "https://www.xiaohongshu.com/"})
    st = extract_state(r.text)
    tabs = ((st.get("user") or {}).get("notes") or [[]])[0] or []
    pairs: dict[str, dict] = {}
    for c in tabs:
        nc = c.get("noteCard") or {}
        nid, tok = nc.get("noteId") or c.get("id"), nc.get("xsecToken") or c.get("xsecToken")
        if nid and tok:
            pairs[nid] = {"token": tok, "title": nc.get("displayTitle"), "time": nc.get("time")}
    print(f"[profile] cards={len(tabs)} pairs={len(pairs)}", flush=True)
    if not pairs:
        raise SystemExit("未拿到 noteId：确认 URL 带 xsec_token 且登录态有效（未登录时 noteId 会被清空）")
    (state / "note_token_pairs.json").write_text(
        json.dumps(pairs, ensure_ascii=False, indent=1), encoding="utf-8")

    # 2) 逐条笔记 SSR
    rows = []
    ordered = sorted(pairs.items(), key=lambda kv: -(kv[1].get("time") or 0))
    for i, (nid, meta) in enumerate(ordered):
        url = (f"https://www.xiaohongshu.com/explore/{nid}"
               f"?xsec_token={meta['token']}&xsec_source=pc_user")
        try:
            resp = creq.get(url, impersonate="chrome", cookies=cookies, timeout=45,
                            headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                                     "Referer": "https://www.xiaohongshu.com/"})
        except Exception as e:
            print(f"[err {i+1}] {nid} {type(e).__name__}", flush=True)
            time.sleep(random.uniform(min_delay + 4, max_delay + 6))
            continue
        note = note_from_html(resp.text, nid)
        imgs = []
        for im in note.get("imageList") or []:
            u = im.get("urlDefault") or im.get("urlPre")
            if not u and im.get("infoList"):
                u = im["infoList"][0].get("url")
            if u:
                imgs.append(u.replace("http://", "https://"))
        rec = {
            "note_id": nid,
            "date_ms": note.get("time") or meta.get("time"),
            "title": note.get("title") or meta.get("title"),
            "desc": note.get("desc"),
            "tags": [t.get("name") for t in (note.get("tagList") or []) if t.get("name")],
            "images": imgs,
            "interact": note.get("interactInfo"),
            "type": note.get("type"),
            "url": url.replace(meta["token"], "REDACTED"),
            "status": resp.status_code,
        }
        rows.append(rec)
        print(f"[note {i+1}/{len(ordered)}] {nid} desc={len(rec['desc'] or '')} imgs={len(imgs)}", flush=True)

        d = state / "images" / nid
        d.mkdir(parents=True, exist_ok=True)
        for j, iu in enumerate(imgs[:12]):
            p = d / f"{j:02d}.webp"
            if p.exists() and p.stat().st_size > 2000:
                continue
            try:
                ir = creq.get(iu, impersonate="safari", cookies=cookies, timeout=45,
                              headers={"User-Agent": UA, "Referer": "https://www.xiaohongshu.com/"})
                if ir.status_code == 200 and len(ir.content) > 1000:
                    p.write_bytes(ir.content)
            except Exception:
                pass
            time.sleep(random.uniform(0.8, 1.8))

        time.sleep(random.uniform(min_delay, max_delay))
        if pause_every and (i + 1) % pause_every == 0:
            print(f"[pause] {pause_s}s", flush=True)
            time.sleep(pause_s)

    rows.sort(key=lambda x: -(x.get("date_ms") or 0))
    (out / "notes_all.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = []
    for rec in rows:
        day = datetime.datetime.fromtimestamp((rec.get("date_ms") or 0) / 1000).strftime("%Y-%m-%d")
        lines.append(f"## {day} | {rec.get('title')}\n\n{(rec.get('desc') or '(无正文)').strip()}\n")
    (out / "notes_digest.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[done] {len(rows)} notes → {out/'notes_all.json'}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="小红书作者主页笔记抓取（页面 SSR 路径）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("login", "fetch"):
        sp = sub.add_parser(name)
        sp.add_argument("--out", default="./xhs_out", help="输出目录（默认 ./xhs_out）")
        if name == "fetch":
            sp.add_argument("--profile-url", required=True, help="主页分享链接，必须带 xsec_token")
            sp.add_argument("--min-delay", type=float, default=4.0)
            sp.add_argument("--max-delay", type=float, default=8.0)
            sp.add_argument("--pause-every", type=int, default=5)
            sp.add_argument("--pause-s", type=int, default=20)
    a = ap.parse_args()
    out = Path(a.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "login":
        cmd_login(out)
    else:
        cmd_fetch(a.profile_url, out, a.min_delay, a.max_delay, a.pause_every, a.pause_s)


if __name__ == "__main__":
    main()
