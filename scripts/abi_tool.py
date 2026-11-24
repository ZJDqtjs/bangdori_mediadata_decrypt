#!/usr/bin/env python3
"""
BanG Dream! (jp.co.craftegg.band) AssetBundleInfo 解析 / 校验 / 还原工具

AssetBundleInfo 结构（protobuf）:
  AssetBundleInfo:
    string      version      = 1   // 例如 10.2.0.230
    repeated    Bundle       bundles = 2
  Bundle:
    string      name         = 1
    BundleDetail detail      = 2
  BundleDetail:
    string      name         = 1   // 与 Bundle.name 相同
    string      sha256       = 2   // 64 hex，同时就是 /data/ 里的文件名
    string      version      = 3   // 内容版本，例如 10.2.0.200
    string      group        = 4   // StartApp / AsNeeded / AdditionalVoice
    uint32      crc32        = 5
    repeated string deps     = 6   // 依赖的 bundle 名
    uint64      size         = 7   // 文件字节数

用法:
  python abi_tool.py csv     [--abi PATH] [-o out.csv]
  python abi_tool.py verify  [--abi PATH] --dir DATA_DIR
  python abi_tool.py rename  [--abi PATH] --dir DATA_DIR --out OUT_DIR
  python abi_tool.py probe   FILE [FILE...]
"""
import argparse
import csv
import math
import os
import sys
import zlib
import hashlib
import collections

# ---------- 极简 protobuf 解码 ----------

def read_varint(data, i):
    r = 0
    s = 0
    while True:
        b = data[i]
        i += 1
        r |= (b & 0x7F) << s
        if not (b & 0x80):
            return r, i
        s += 7
        if s > 70:
            raise ValueError("varint too long")


def parse_message(data):
    i, n, out = 0, len(data), []
    while i < n:
        key, i = read_varint(data, i)
        fnum, wire = key >> 3, key & 7
        if fnum == 0:
            raise ValueError("bad field 0")
        if wire == 0:
            v, i = read_varint(data, i)
            out.append((fnum, 0, v))
        elif wire == 2:
            ln, i = read_varint(data, i)
            if i + ln > n:
                raise ValueError("truncated")
            out.append((fnum, 2, data[i:i + ln]))
            i += ln
        elif wire == 5:
            out.append((fnum, 5, data[i:i + 4]))
            i += 4
        elif wire == 1:
            out.append((fnum, 1, data[i:i + 8]))
            i += 8
        else:
            raise ValueError(f"unsupported wire {wire}")
    return out


def load_abi(path):
    data = open(path, "rb").read()
    top = parse_message(data)
    version = None
    entries = []
    for f, w, v in top:
        if f == 1 and w == 2:
            version = v.decode("utf-8", "replace")
        elif f == 2 and w == 2:
            e = {"name": "", "sha256": "", "version": "", "group": "",
                 "crc32": 0, "size": 0, "deps": []}
            for f2, w2, v2 in parse_message(v):
                if f2 == 1 and w2 == 2:
                    e["name"] = v2.decode("utf-8", "replace")
                elif f2 == 2 and w2 == 2:
                    for f3, w3, v3 in parse_message(v2):
                        if f3 == 1 and w3 == 2:
                            e["name"] = v3.decode("utf-8", "replace")
                        elif f3 == 2 and w3 == 2:
                            e["sha256"] = v3.decode()
                        elif f3 == 3 and w3 == 2:
                            e["version"] = v3.decode()
                        elif f3 == 4 and w3 == 2:
                            e["group"] = v3.decode()
                        elif f3 == 5 and w3 == 0:
                            e["crc32"] = v3
                        elif f3 == 6 and w3 == 2:
                            e["deps"].append(v3.decode("utf-8", "replace"))
                        elif f3 == 7 and w3 == 0:
                            e["size"] = v3
            entries.append(e)
    return version, entries


# ---------- 子命令 ----------

def cmd_csv(args):
    version, es = load_abi(args.abi)
    out = args.out or "AssetBundleInfo.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["name", "filename(sha256)", "content_version", "group",
                    "crc32", "size_bytes", "size_kib", "deps"])
        for e in es:
            w.writerow([e["name"], e["sha256"], e["version"], e["group"],
                        e["crc32"], e["size"], round(e["size"] / 1024, 2),
                        ";".join(e["deps"])])
    print(f"catalog version {version}, {len(es)} entries -> {out}")


def cmd_verify(args):
    version, es = load_abi(args.abi)
    by_name = {e["sha256"]: e for e in es}
    seen = set()
    bad_size, bad_sha, bad_crc, extra = [], [], [], []
    files = [f for f in os.listdir(args.dir)
             if os.path.isfile(os.path.join(args.dir, f))]
    for fn in files:
        p = os.path.join(args.dir, fn)
        e = by_name.get(fn)
        if not e:
            extra.append(fn)
            continue
        seen.add(fn)
        st = os.path.getsize(p)
        if st != e["size"]:
            bad_size.append((fn, st, e["size"]))
            continue
        h = hashlib.sha256()
        crc = 0
        with open(p, "rb") as fp:
            while True:
                b = fp.read(1 << 20)
                if not b:
                    break
                h.update(b)
                crc = zlib.crc32(b, crc) & 0xFFFFFFFF
        if h.hexdigest() != e["sha256"]:
            bad_sha.append(fn)
        if crc != e["crc32"]:
            bad_crc.append((fn, crc, e["crc32"]))
    missing = [e["name"] for e in es if e["sha256"] not in seen]
    print(f"catalog {version}: {len(es)} entries / {len(files)} files on disk")
    print(f"  size mismatch: {len(bad_size)}")
    for x in bad_size[:10]:
        print("    ", x)
    print(f"  sha256 mismatch: {len(bad_sha)}")
    for x in bad_sha[:10]:
        print("    ", x)
    print(f"  crc32 mismatch: {len(bad_crc)}")
    for x in bad_crc[:10]:
        print("    ", x)
    print(f"  missing: {len(missing)}")
    for x in missing[:10]:
        print("    ", x)
    print(f"  extra (not in catalog): {len(extra)}")
    for x in extra[:10]:
        print("    ", x)


def cmd_rename(args):
    version, es = load_abi(args.abi)
    by_sha = {e["sha256"]: e for e in es}
    os.makedirs(args.out, exist_ok=True)
    done = 0
    for fn in os.listdir(args.dir):
        src = os.path.join(args.dir, fn)
        if not os.path.isfile(src):
            continue
        e = by_sha.get(fn)
        if not e:
            print("  skip (not in catalog):", fn)
            continue
        dst_name = e["name"].replace("/", "@") + ".bundle"
        dst = os.path.join(args.out, dst_name)
        if args.move:
            os.replace(src, dst)
        else:
            import shutil
            shutil.copyfile(src, dst)
        done += 1
    print(f"restored {done} files -> {args.out}")


MAGICS = [
    (b"UnityFS", "UnityFS AssetBundle"),
    (b"UnityWeb", "UnityWebData (压缩的 web bundle)"),
    (b"UnityRaw", "UnityRaw"),
    (b"UnityArchive", "UnityArchive"),
    (b"CAB-", "CAB- (Unity 内置 AssetBundle 命名)"),
    (b"PK\x03\x04", "ZIP"),
    (b"\x1f\x8b\x08", "GZIP"),
    (b"Rar!", "RAR"),
]


def entropy(b):
    if not b:
        return 0.0
    c = collections.Counter(b)
    n = len(b)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def cmd_probe(args):
    for path in args.files:
        st = os.path.getsize(path)
        d = open(path, "rb").read()
        head = d[:64]
        print(f"\n=== {os.path.basename(path)}  ({st} bytes)")
        print("  head hex:", head[:32].hex(" "))
        magic = None
        for m, label in MAGICS:
            if d.startswith(m):
                magic = label
        if magic:
            print("  magic:", magic)
        else:
            # 是否在偏移处出现 UnityFS（说明前面被加了壳）
            for off in range(1, min(4096, max(1, len(d) - 8))):
                if d[off:off + 7] == b"UnityFS":
                    print(f"  'UnityFS' 出现在偏移 {off}（可能被加了前置头）")
                    break
            else:
                print("  magic: 未识别（不是标准 UnityFS/UnityWeb 头）")
        print(f"  熵(前4KB)={entropy(d[:4096]):.3f}  熵(全文)={entropy(d):.3f}  (8.0 表示接近随机/已压缩或加密)")
        print(f"  唯一字节数(前4KB)={len(set(d[:4096]))}")
        # 常见字符串线索
        for kw in (b"UnityFS", b"CAB-", b"2020.3", b"2021.3", b"il2cpp", b"Assembly-CSharp"):
            if kw in d[:200]:
                print("  命中特征:", kw)
        if st > 64:
            print("  尾部 32 字节:", d[-32:].hex(" "))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("csv")
    p.add_argument("--abi", default="AssetBundleInfo")
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_csv)

    p = sub.add_parser("verify")
    p.add_argument("--abi", default="AssetBundleInfo")
    p.add_argument("--dir", required=True)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("rename")
    p.add_argument("--abi", default="AssetBundleInfo")
    p.add_argument("--dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--move", action="store_true", help="移动而不是复制")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("probe")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_probe)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
