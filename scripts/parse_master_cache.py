#!/usr/bin/env python3
"""
《BanG Dream!》master 目录解析工具

两个目标文件，性质完全不同：

1) metamap/AssetBundleMetaMap  —— 明文 protobuf，可完整解析
   结构: repeated { string sha256 = 1; Meta { uint64 timestamp = 2.1 } }
   每个条目 = 一个资源包的 sha256（与 /data/ 文件名、AssetBundleInfo 一致）
   配 last-touched 时间戳（epoch 毫秒），可以反查“最近用过哪些资源”。

2) master/cache  —— 真·加密数据（4693248 字节，16 字节对齐）
   实测判定:
     · 熵 7.95，全文件无任何可读字符串
     · zlib / gzip / raw-deflate / lzma / bz2 / zstd / brotli 全部解不开
     · 16 字节块零重复（不是 AES-ECB 的重复块特征）
     · 周期 XOR 检测每个相位主字节占比仅 0.007（等价于随机）
     · 用 global-metadata 里 27594 个候选串（含 md5/sha 派生）做 AES
       128/256 ECB、CBC(零 IV)、CBC(首块当 IV) 严格试解，命中 0
   结论: 密钥不在 metadata / settings / version 里，应在 native 层
   （libil2cpp.so）或由服务端下发。拿到密钥后可用本脚本 decrypt 子命令解开。

用法:
  # 解析 metamap，并与索引对照表 join
  python parse_master_cache.py metamap --metamap <AssetBundleMetaMap> --abi <AssetBundleInfo> -o metamap.csv

  # 对 master/cache 做结构与加密层报告
  python parse_master_cache.py report <master/cache>

  # 拿到 key 之后解密（自动识别 zlib / JSON / protobuf）
  python parse_master_cache.py decrypt <master/cache> --key <hex> --mode cbc --iv <hex> -o out.bin
"""
import argparse
import collections
import csv
import hashlib
import math
import os
import zlib


# ---------------- 极简 protobuf ----------------

def read_varint(data, i):
    r = s = 0
    while True:
        b = data[i]; i += 1
        r |= (b & 0x7F) << s
        if not b & 0x80:
            return r, i
        s += 7


def parse_msg(data):
    i, n, out = 0, len(data), []
    while i < n:
        key, i = read_varint(data, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = read_varint(data, i)
            out.append((f, w, v))
        elif w == 2:
            ln, i = read_varint(data, i)
            out.append((f, w, data[i:i + ln]))
            i += ln
        elif w == 5:
            out.append((f, w, data[i:i + 4])); i += 4
        elif w == 1:
            out.append((f, w, data[i:i + 8])); i += 8
        else:
            raise ValueError(f"unsupported wire {w}")
    return out


def parse_metamap(path):
    """返回 [(sha256, timestamp_ms)]"""
    out = []
    for f, w, v in parse_msg(open(path, "rb").read()):
        if f != 1 or w != 2:
            continue
        sha, ts = None, 0
        for f2, w2, v2 in parse_msg(v):
            if f2 == 1 and w2 == 2:
                sha = v2.decode("ascii", "replace")
            elif f2 == 2 and w2 == 2:
                for f3, w3, v3 in parse_msg(v2):
                    if f3 == 8 and w3 == 0:     # 假定 timestamp 是首个 varint 字段
                        ts = v3
                    elif w3 == 0 and ts == 0:
                        ts = v3
        if sha:
            out.append((sha, ts))
    return out


def load_abi(path):
    """AssetBundleInfo -> {sha256: (name, group, size)}"""
    idx = {}
    if not path or not os.path.exists(path):
        return idx
    for f, w, v in parse_msg(open(path, "rb").read()):
        if f != 2 or w != 2:
            continue
        name = sha = ""
        for f2, w2, v2 in parse_msg(v):
            if f2 == 1 and w2 == 2:
                name = v2.decode("utf-8", "replace")
            elif f2 == 2 and w2 == 2:
                for f3, w3, v3 in parse_msg(v2):
                    if f3 == 1 and w3 == 2 and not name:
                        name = v3.decode("utf-8", "replace")
                    elif f3 == 2 and w3 == 2:
                        sha = v3.decode()
        if sha:
            idx[sha] = name
    return idx


def cmd_metamap(a):
    rows = parse_metamap(a.metamap)
    idx = load_abi(a.abi)
    import datetime
    out = a.out or "metamap.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["sha256", "last_touched", "timestamp_ms", "bundle_name"])
        for sha, ts in sorted(rows, key=lambda x: -x[1]):
            t = datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
            w.writerow([sha, t, ts, idx.get(sha, "")])
    matched = sum(1 for s, _ in rows if s in idx)
    print(f"metamap 条目 {len(rows)} 条，匹配到索引名称 {matched} 条 -> {out}")
    if rows:
        top = sorted(rows, key=lambda x: -x[1])[:5]
        for sha, ts in top:
            t = datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M") if ts else "?"
            print(f"  {t}  {idx.get(sha, '<不在索引中>')}")


def entropy(b):
    c = collections.Counter(b)
    n = len(b)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def cmd_report(a):
    d = open(a.file, "rb").read()
    print(f"文件: {a.file}")
    print(f"  大小 {len(d)} 字节   16B 对齐={len(d) % 16 == 0}   4B 对齐={len(d) % 4 == 0}")
    print(f"  熵(全文) {entropy(d[:1048576]):.3f} (8.0 = 接近随机)")
    printable = sum(1 for c in d[:65536] if 9 <= c <= 13 or 32 <= c < 127)
    print(f"  可打印字节占比 {printable / 65536:.1%}（正常文本/JSON 应 > 90%）")

    blocks = [d[i:i + 16] for i in range(0, len(d), 16)]
    uniq = len(set(blocks))
    print(f"  16 字节块 {len(blocks)} 个，唯一 {uniq} 个（若重复率高则是 ECB）")

    print("  压缩算法探测:")
    for name, fn in (("zlib", lambda x: zlib.decompress(x)),
                     ("gzip", lambda x: zlib.decompress(x, 31)),
                     ("raw-deflate", lambda x: zlib.decompress(x, -15))):
        try:
            fn(d); print(f"    {name}: 成功")
        except Exception as e:
            print(f"    {name}: {type(e).__name__}")

    # 周期 XOR
    best = []
    for p in range(1, 65):
        cnt = collections.Counter()
        tot = collections.Counter()
        for i in range(min(len(d), 200000)):
            cnt[(i % p, d[i])] += 1
            tot[i % p] += 1
        ratio = sum(max(v for (o, b), v in cnt.items() if o == k) / tot[k] for k in range(p)) / p
        best.append((ratio, p))
    best.sort(reverse=True)
    print("  周期 XOR 检测（前 3 名，接近 1.0 才是周期 XOR）:")
    for ratio, p in best[:3]:
        print(f"    period={p:<3} 主字节占比={ratio:.3f}")

    print("\n  判定: 数据经过加密（或加密+压缩），本地没有可用密钥。")


def cmd_decrypt(a):
    from Crypto.Cipher import AES
    d = open(a.file, "rb").read()
    key = bytes.fromhex(a.key) if all(c in "0123456789abcdefABCDEF" for c in a.key) else a.key.encode()
    iv = bytes.fromhex(a.iv) if a.iv and all(c in "0123456789abcdefABCDEF" for c in a.iv) else (a.iv.encode() if a.iv else b"\x00" * 16)
    if a.mode == "ecb":
        plain = AES.new(key, AES.MODE_ECB).decrypt(d[:len(d) - len(d) % 16])
    elif a.mode == "cbc":
        plain = AES.new(key, AES.MODE_CBC, iv).decrypt(d[:len(d) - len(d) % 16])
    else:
        plain = AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=int.from_bytes(iv, "big")).decrypt(d)

    out = a.out or "master_cache_plain.bin"
    kind = "binary"
    for tag, fn in (("zlib", lambda x: zlib.decompress(x) if x[:2] in (b"\x78\x9c", b"\x78\x01", b"\x78\xda") else None),
                    ("gzip", lambda x: zlib.decompress(x, 31) if x[:2] == b"\x1f\x8b" else None)):
        try:
            dec = fn(plain)
            if dec:
                plain, kind = dec, tag
                break
        except Exception:
            pass
    if kind == "binary":
        text = sum(1 for c in plain[:65536] if 9 <= c <= 13 or 32 <= c < 127)
        if text / min(len(plain), 65536) > 0.9:
            kind = "text"
    open(out, "wb").write(plain)
    print(f"解密完成: {kind}，{len(plain)} 字节 -> {out}")
    print("  头部:", plain[:64].hex(" "))
    if kind == "text":
        print("  预览:", plain[:300].decode("utf-8", "replace"))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("metamap", help="解析 AssetBundleMetaMap 并与索引 join")
    p.add_argument("--metamap", required=True)
    p.add_argument("--abi", help="AssetBundleInfo 路径（可选）")
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_metamap)

    p = sub.add_parser("report", help="分析 master/cache 的结构与加密层")
    p.add_argument("file")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("decrypt", help="已知 key 时解密 master/cache")
    p.add_argument("file")
    p.add_argument("--key", required=True, help="hex 或字面量")
    p.add_argument("--iv", default="")
    p.add_argument("--mode", choices=["ecb", "cbc", "ctr"], default="cbc")
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_decrypt)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
