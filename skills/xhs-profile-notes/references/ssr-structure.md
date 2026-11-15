# 小红书页面 SSR 结构（实测 2026-11）

## 主页

`GET https://www.xiaohongshu.com/user/profile/{user_id}?xsec_token={profile_token}&xsec_source=app_share`

登录态下 HTML 里：

```js
window.__INITIAL_STATE__ = { user: { notes: [ [ /* tab0: 笔记卡片 */ ], [], ... ], ... }, ... }
```

每张卡片：

```json
{
  "id": "",
  "noteCard": {
    "noteId": "6970d0e0000000000e03fa28",
    "xsecToken": "<该笔记自己的 token>",
    "displayTitle": "…",
    "time": 1769001184000,
    "interactInfo": {"likedCount": "163", "collectedCount": "…"},
    "cover": {"infoList": [{"url": "https://sns-webpic-qc.xhscdn.com/…"}]}
  },
  "xsecToken": "<外层同名 token>"
}
```

**未登录**时 `noteId` 与 `xsecToken` 仍会出现，但 `noteId` 是空串——这是最容易误判「抓到了」的坑。

## 笔记详情

`GET https://www.xiaohongshu.com/explore/{note_id}?xsec_token={note_token}&xsec_source=pc_user`

- 整页 `__INITIAL_STATE__` **不是合法 JSON**：里面混着 `new Map([])`（`AiNoteDetailStore`）、`undefined` 等 JS 字面量。
- 只切 `"noteDetailMap":` 后面的对象：

```python
KEY = '"noteDetailMap":'

def slice_object(s, start):
    depth = 0; instr = esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if instr:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': instr = False
            continue
        if ch == '"': instr = True
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0: return s[start:i+1]
    return None

def note_from_html(html, nid):
    pos = -1
    while True:
        pos = html.find(KEY, pos + 1)
        if pos < 0: return {}
        raw = slice_object(html, pos + len(KEY))
        if not raw: continue
        try: obj = json.loads(raw)
        except Exception: continue
        if nid in obj: return (obj[nid] or {}).get("note") or {}
```

笔记对象字段：`noteId / title / desc / time / type / tagList[].name / imageList[].urlDefault|urlPre|infoList[].url / interactInfo / user`。
注意是 **camelCase**（`imageList` 而不是 `image_list`）。

## 图片

封面/正文图直链在 `sns-webpic-qc.xhscdn.com`，需要 `Referer: https://www.xiaohongshu.com/` 与登录 cookie；返回 webp。

## 数据形态提示

- 正文常很短（图片才是主体），`desc` 为空是正常的，不代表抓失败。
- `time` 是毫秒时间戳；早期笔记出现过 `0`（缺时间），排序时会把它们排到最后。
- 互动数可能是空串，不要当 0 处理。
