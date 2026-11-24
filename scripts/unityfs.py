import struct, sys, os
import lz4.block


class Reader:
    def __init__(self, d):
        self.d = d
        self.i = 0

    def u32(self):
        v = struct.unpack('>I', self.d[self.i:self.i+4])[0]; self.i += 4; return v

    def i64(self):
        v = struct.unpack('>q', self.d[self.i:self.i+8])[0]; self.i += 8; return v

    def cstr(self):
        j = self.d.index(b'\0', self.i)
        s = self.d[self.i:j]; self.i = j + 1
        return s


def lz4_decompress(src, usize):
    return lz4.block.decompress(src, uncompressed_size=usize)


def lzma_decompress(src, usize):
    import lzma
    return lzma.decompress(src)


def unpack(path):
    d = open(path, 'rb').read()
    assert d[:8] == b'UnityFS\0', 'not UnityFS'
    r = Reader(d); r.i = 8
    ver = r.u32()
    uver = r.cstr().decode('utf-8', 'replace')
    urev = r.cstr().decode('utf-8', 'replace')
    size = r.i64()
    cbis = r.u32()
    ubis = r.u32()
    flags = r.u32()
    if ver >= 7:
        r.i = (r.i + 15) & ~15
    compression = flags & 0x3f
    if flags & 0x400:
        raise RuntimeError('UnityCN encryption flag set (0x400)')
    raw_info = d[r.i:r.i+cbis]
    if compression == 1:
        info = lzma_decompress(raw_info, ubis)
    elif compression in (2, 3):
        info = lz4_decompress(raw_info, ubis)
    elif compression == 0:
        info = raw_info
    else:
        raise RuntimeError(f'unknown compression {compression}')
    ri = Reader(info)
    hashu = ri.d[ri.i:ri.i+16]; ri.i += 16
    bcount = ri.u32()
    blocks = []
    for _ in range(bcount):
        us = ri.u32(); cs = ri.u32(); fl = struct.unpack('>H', ri.d[ri.i:ri.i+2])[0]; ri.i += 2
        blocks.append((us, cs, fl))
    ncount = ri.u32()
    nodes = []
    for _ in range(ncount):
        off = ri.i64(); sz = ri.i64(); fl = ri.u32(); p = ri.cstr().decode('utf-8', 'replace')
        nodes.append((off, sz, fl, p))

    data_start = (r.i + cbis + 15) & ~15
    buf = bytearray()
    pos = data_start
    for us, cs, fl in blocks:
        comp = fl & 0x3f
        chunk = d[pos:pos+cs]; pos += cs
        if comp == 1:
            buf += lzma_decompress(chunk, us)
        elif comp in (2, 3):
            buf += lz4_decompress(chunk, us)
        elif comp == 0:
            buf += chunk
        else:
            raise RuntimeError(f'unknown block compression {comp}')
    files = {}
    for off, sz, fl, p in nodes:
        files[p or f'<block{len(files)}>'] = bytes(buf[off:off+sz])
    return dict(ver=ver, uver=uver, urev=urev, size=size, flags=flags,
                compression=compression, blocks=blocks, nodes=nodes, files=files)


if __name__ == '__main__':
    import re
    p = sys.argv[1]
    b = unpack(p)
    print(f"header: ver={b['ver']} unity={b['uver']}/{b['urev']} flags={hex(b['flags'])} "
          f"comp={b['compression']} blocks={len(b['blocks'])} files={len(b['nodes'])}")
    for name, data in b['files'].items():
        print(f"  file: {name}  {len(data)} bytes  head={data[:16].hex(' ')}")
        for m in re.finditer(rb'(20\d\d\.\d+\.\d+[a-z]*\d*)', data[:2000]):
            print("    unity version string:", m.group(1).decode())
