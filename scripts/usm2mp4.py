#!/usr/bin/env python3
"""
CRI USM -> MP4 转换器（针对《BanG Dream!》mf/*.usm.bytes）

文件结构（实测）:
  CRID 头: 'CRID' + u32 header_len + 版本 + u32 字段 ...，随后是头部长度处的
           '@SFV' 块（Sofdec2 stream header + seek 表）
  块序列: 4 字节块类型 + u32 块长度 + 块体
          块体前 24 字节是块内头（u16 0x0018 等），其后是裸 H.264 数据，
          每帧以 00 00 00 01 09（AUD）开头
  实测: 336 个块，其中 331 个为视频块 -> 331 帧，H.264 High 448x336 25fps

本脚本:
  1. 解析 CRID/@SFV 与块序列；
  2. 逐个视频块剥离块内头，拼出 H.264 裸流（.264）；
  3. 若本机有 ffmpeg，直接无损封装为 MP4（-c copy）。

用法:
  python usm2mp4.py <file.usm.bytes> [more...]        # 单个/多个文件
  python usm2mp4.py --dir <mf目录> --out <输出目录>     # 批量
  python usm2mp4.py --dir <mf目录> --raw                # 只抽 .264 不封装
"""
import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

START_CODE = b"\x00\x00\x00\x01"
VIDEO_MARKERS = (b"@UTF", b"#HEADER", b"#METADATA", b"@AHX", b"@ALU")


def find_ffmpeg():
    for name in ("ffmpeg", "ffmpeg.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def parse_header(data):
    """解析 CRID 头，返回 (header_len, 版本字段)"""
    if data[:4] != b"CRID":
        raise ValueError("不是 CRI USM 文件（缺少 CRID 魔数）")
    header_len = struct.unpack(">I", data[4:8])[0]
    return header_len, data[8:12].hex()


def find_first_chunk(data, header_len):
    """块序列起点：头长度之后第一个块签名（通常有 8 字节对齐填充）"""
    for sig in (b"@SFV", b"@UTF", b"@VID", b"@AUD"):
        i = data.find(sig, header_len, header_len + 128)
        if i >= 0:
            return i
    return header_len


def iter_chunks(data, header_len):
    pos = find_first_chunk(data, header_len)
    total = len(data)
    while pos + 8 <= total:
        sig = data[pos:pos + 4]
        size = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        if size > total - pos - 8 or size == 0:
            break
        yield sig, data[pos + 8:pos + 8 + size]
        pos += 8 + size


def video_payload(body):
    """从块体里切出 H.264 数据；不是视频块就返回 None

    视频块块内头（24 字节，实测）:
      u16 0x0018(24) | u16 本帧 NAL 数 | u32 预留 | u32 pts(每帧 +50) | u32 fps*100(6000)
    """
    for marker in VIDEO_MARKERS:
        if body[:len(marker)] == marker or body[24:24 + len(marker)] == marker:
            return None
    idx = body.find(START_CODE, 0, 1024)
    if idx < 0:
        return None
    return body[idx:]


def chunk_fps(body):
    """从块内头读 FPS（u32 fps*100），读不到返回 None"""
    if len(body) < 16:
        return None
    v = struct.unpack(">I", body[12:16])[0]
    if 1000 <= v <= 12000:
        return round(v / 100.0, 2)
    return None


def demux(path):
    data = open(path, "rb").read()
    header_len, hver = parse_header(data)
    chunks = list(iter_chunks(data, header_len))
    video, fps = [], None
    kinds = {}
    for sig, body in chunks:
        kinds[sig] = kinds.get(sig, 0) + 1
        p = video_payload(body)
        if p:
            video.append(p)
            if fps is None:
                fps = chunk_fps(body)
    return b"".join(video), dict(header_len=header_len, header_ver=hver,
                                chunks=len(chunks), video_chunks=len(video),
                                kinds=kinds, fps=fps)



def probe(ffmpeg, es_path):
    ffprobe = None
    for name in ("ffprobe", "ffprobe.exe"):
        ffprobe = shutil.which(name)
        if ffprobe:
            break
    if not ffprobe:
        return ""
    try:
        extra = ["-f", "h264", "-count_frames"] if es_path.endswith(".264") else []
        out = subprocess.run(
            [ffprobe, "-hide_banner", "-v", "error"] + extra + ["-i", es_path,
             "-show_entries", "stream=codec_name,width,height,profile,avg_frame_rate,nb_read_frames"] + extra,
            capture_output=True, text=True, timeout=180).stdout
        vals = dict(re.findall(r"(codec_name|width|height|profile|avg_frame_rate|nb_read_frames)=(.+)", out))
        return " ".join(f"{k}={v}" for k, v in vals.items())
    except Exception:
        return ""


def convert(path, outdir, ffmpeg, keep_raw):
    name = os.path.splitext(os.path.basename(path))[0]
    if name.endswith(".usm"):
        name = name[:-4]
    es, meta = demux(path)
    if not es:
        return None, meta
    os.makedirs(outdir, exist_ok=True)
    es_path = os.path.join(outdir, name + ".264")
    open(es_path, "wb").write(es)
    result = es_path
    if ffmpeg and not keep_raw:
        mp4_path = os.path.join(outdir, name + ".mp4")
        fr = str(meta.get("fps")) if meta.get("fps") else "30"
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "h264", "-framerate", fr, "-i", es_path,
               "-c", "copy", "-movflags", "+faststart", mp4_path]
        rc = subprocess.run(cmd, capture_output=True, text=True)
        if rc.returncode == 0 and os.path.exists(mp4_path):
            os.remove(es_path)
            result = mp4_path
        else:
            print(f"    ! ffmpeg 封装失败，保留 .264: {rc.stderr.strip()[:200]}")
    return result, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", help="批量处理目录（如 mf/）")
    ap.add_argument("--out", default="mp4")
    ap.add_argument("--raw", action="store_true", help="只抽 .264，不封装 MP4")
    a = ap.parse_args()

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("警告: 未找到 ffmpeg，将只输出 .264 裸流（可用 ffmpeg -f h264 -i x.264 -c copy x.mp4 自行封装）")

    targets = list(a.files)
    if a.dir:
        targets += [os.path.join(a.dir, f) for f in sorted(os.listdir(a.dir))
                    if f.lower().endswith((".usm", ".bytes", ".usm.bytes"))]
    if not targets:
        ap.print_help()
        return 1

    ok = fail = 0
    for p in targets:
        try:
            res, meta = convert(p, a.out, ffmpeg, a.raw)
            if res:
                ok += 1
                print(f"  [OK] {os.path.basename(p)} -> {os.path.basename(res)}  "
                      f"chunks={meta['chunks']} video={meta['video_chunks']}  {probe(ffmpeg, res)}")
            else:
                fail += 1
                print(f"  [SKIP] {os.path.basename(p)} 未找到 H.264 数据（chunk 类型 {meta['kinds']}）")
        except Exception as e:
            fail += 1
            print(f"  [FAIL] {os.path.basename(p)} {type(e).__name__}: {e}")
    print(f"\n完成: 成功 {ok} / 跳过或失败 {fail}  ->  {os.path.abspath(a.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
