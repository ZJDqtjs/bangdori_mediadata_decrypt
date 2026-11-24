#!/usr/bin/env python3
"""
BanG Dream! (jp.co.craftegg.band) 资源包批量还原 / 导出

背景:
  /data/ 下的无扩展名文件是标准 UnityFS（LZ4HC 压缩），并没有加密。
  唯一的“保护”是把 UnityFS 头里的 unityVersion / unityRevision 改成了
  "5.x.x" / "0.0.0"，导致 UnityPy / AssetStudio 拿不到版本号而报错。
  这里设置 FALLBACK_UNITY_VERSION 绕过；实测 2021.3 与 2022.3 解析结果一致。

用法:
  python bangdream_extract.py --dir <data目录> --out <输出目录>
  python bangdream_extract.py --dir <data目录> --out <输出目录> --what texture
  python bangdream_extract.py --dir <data目录> --list      # 只列结构不导出
"""
import argparse
import json
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")

import UnityPy

UNITY_VERSION = "2022.3.10f1"

SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(s, fallback="unnamed"):
    s = (s or fallback).strip()
    s = SAFE.sub("_", s.replace("\\", "/"))
    return s or fallback


def logical_name(env, fn):
    for o in env.objects:
        if o.type.name == "AssetBundle":
            try:
                return o.read().m_Name
            except Exception:
                pass
    return fn


def dump_mono(data):
    """把 MonoBehaviour 的结构尽量序列化成 JSON"""
    def conv(v):
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        if isinstance(v, (bytes, bytearray)):
            return f"<bytes {len(v)}>"
        if isinstance(v, (list, tuple)):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {str(k): conv(x) for k, x in v.items()}
        return str(v)
    if hasattr(data, "__dict__"):
        return {k: conv(v) for k, v in data.__dict__.items() if not k.startswith("_")}
    return str(data)


def export_one(path, outroot, what, listing):
    env = UnityPy.load(path)
    name = logical_name(env, os.path.basename(path))
    outdir = os.path.join(outroot, *[safe_name(x) for x in name.split("/")])
    counts = {}
    for o in env.objects:
        t = o.type.name
        counts[t] = counts.get(t, 0) + 1
        if listing:
            continue
        try:
            if t == "Texture2D" and what in ("all", "texture"):
                data = o.read()
                os.makedirs(os.path.join(outdir, "textures"), exist_ok=True)
                data.image.save(os.path.join(outdir, "textures", safe_name(data.m_Name) + ".png"))
            elif t == "TextAsset" and what in ("all", "text"):
                data = o.read()
                os.makedirs(os.path.join(outdir, "text"), exist_ok=True)
                nm = safe_name(data.m_Name)
                raw = data.m_Script
                if isinstance(raw, str):
                    raw = raw.encode("utf-8", "surrogateescape")
                open(os.path.join(outdir, "text", nm), "wb").write(bytes(raw))
            elif t == "AudioClip" and what in ("all", "audio"):
                data = o.read()
                os.makedirs(os.path.join(outdir, "audio"), exist_ok=True)
                for fname, fdata in data.samples.items():
                    open(os.path.join(outdir, "audio", safe_name(fname)), "wb").write(fdata)
            elif t == "MonoBehaviour" and what in ("all", "json"):
                data = o.read()
                os.makedirs(os.path.join(outdir, "mono"), exist_ok=True)
                nm = safe_name(getattr(data, "m_Name", f"mono_{o.path_id}"))
                json.dump(dump_mono(data), open(os.path.join(outdir, "mono", nm + ".json"), "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
        except Exception as e:
            print(f"    ! {t} 导出失败: {e}")
    return name, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default="提取产物")
    ap.add_argument("--what", default="all", choices=["all", "texture", "text", "audio", "json"])
    ap.add_argument("--list", action="store_true", help="只列出内部结构")
    ap.add_argument("--version", default=UNITY_VERSION)
    a = ap.parse_args()

    UnityPy.config.FALLBACK_UNITY_VERSION = a.version
    files = [f for f in sorted(os.listdir(a.dir)) if os.path.isfile(os.path.join(a.dir, f))]
    ok = fail = skip = 0
    for fn in files:
        p = os.path.join(a.dir, fn)
        if os.path.getsize(p) == 0:
            skip += 1
            continue
        if open(p, "rb").read(7) != b"UnityFS":
            skip += 1
            continue
        try:
            name, counts = export_one(p, a.out, a.what, a.list)
            ok += 1
            print(f"  [{ok}] {fn[:12]}…  ->  {name}   {counts}")
        except Exception as e:
            fail += 1
            print(f"  [FAIL] {fn[:12]}…  {type(e).__name__}: {e}")
    print(f"\n完成: 成功 {ok} / 失败 {fail} / 跳过(非 UnityFS 或空文件) {skip}")
    if not a.list:
        print(f"导出目录: {os.path.abspath(a.out)}")


if __name__ == "__main__":
    main()
