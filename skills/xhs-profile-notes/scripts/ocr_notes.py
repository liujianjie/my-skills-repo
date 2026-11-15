#!/usr/bin/env python3
"""对抓下来的笔记图片做 OCR。

  python ocr_notes.py --out ./xhs_out --lang chi_sim+eng --psm 6
输入：<out>/.state/images/<note_id>/*.webp
输出：<out>/ocr/<note_id>_<idx>.txt
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./xhs_out")
    ap.add_argument("--lang", default="chi_sim+eng")
    ap.add_argument("--psm", default="6")
    ap.add_argument("--tess", default=os.environ.get("TESSERACT", "tesseract"))
    a = ap.parse_args()

    out = Path(a.out).expanduser().resolve()
    tess = shutil.which(a.tess) or a.tess
    if not Path(tess).exists() and not shutil.which(a.tess):
        raise SystemExit("tesseract 不可用：用 --tess 指定可执行文件路径")

    src = out / ".state" / "images"
    dst = out / "ocr"
    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(src.glob("*/*"))
    print(f"[ocr] {len(files)} images from {src}")
    new = 0
    for f in files:
        if f.suffix.lower() not in {".webp", ".jpg", ".jpeg", ".png"}:
            continue
        target = dst / f"{f.parent.name}_{f.stem}"
        if Path(str(target) + ".txt").exists():
            continue
        subprocess.run([tess, str(f), str(target), "-l", a.lang, "--psm", a.psm], capture_output=True)
        new += 1
    print(f"[ocr] new={new} total={len(list(dst.glob('*.txt')))} → {dst}")


if __name__ == "__main__":
    main()
