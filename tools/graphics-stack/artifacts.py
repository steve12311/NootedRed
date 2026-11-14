"""Local component packaging helpers. No network, installation or EFI access."""
from pathlib import Path
import hashlib
import json
import plistlib
import re
import shutil
import struct
import subprocess
import tempfile
import uuid


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def commands(data):
    if len(data) < 32 or struct.unpack_from('<I', data)[0] != 0xfeedfacf:
        raise ValueError('Expected an uncompressed x86-64 Mach-O')
    pos = 32
    end = pos + struct.unpack_from('<I', data, 20)[0]
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        if pos + 8 > end:
            raise ValueError('Truncated load command')
        cmd, size = struct.unpack_from('<II', data, pos)
        if size < 8 or pos + size > end:
            raise ValueError('Invalid load command')
        yield pos, cmd, size
        pos += size


def thin(data):
    if data[:4] == b'\xca\xfe\xba\xbe':
        for i in range(struct.unpack_from('>I', data, 4)[0]):
            cpu, _, off, size, _ = struct.unpack_from('>IIIII', data, 8 + i * 20)
            if cpu == 0x1000007:
                return data[off:off + size]
        raise ValueError('No x86-64 slice')
    return data


def macho_uuid(path):
    data = thin(Path(path).read_bytes())
    for pos, cmd, _ in commands(data):
        if cmd == 0x1b:
            return str(uuid.UUID(bytes=data[pos + 8:pos + 24]))
    raise ValueError('No Mach-O UUID')


def sections(data):
    result = []
    for pos, cmd, _ in commands(data):
        if cmd != 0x19:
            continue
        for i in range(struct.unpack_from('<I', data, pos + 64)[0]):
            p = pos + 72 + i * 80
            name = data[p:p + 16].split(b'\0')[0].decode()
            vm, size, off = struct.unpack_from('<QQI', data, p + 32)
            result.append((name, vm, size, off))
    return result


def array(text, name):
    match = re.search(r'\b' + name + r'\[\]\s*=\s*\{(.*?)\};', text, re.S)
    if not match:
        raise ValueError('Missing patch array: ' + name)
    return bytes(int(x, 16) for x in re.findall(r'0x([0-9A-Fa-f]{2})\b', match[1]))


def sparse_layout(data, output):
    data = bytearray(data)
    cmds = list(commands(data))
    segments = []
    for p, c, _ in cmds:
        if c == 0x19:
            vm, vs, off, length = struct.unpack_from('<QQQQ', data, p + 24)
            segments.append(dict(pos=p, name=data[p + 8:p + 24].rstrip(b'\0').decode(),
                                 vm=vm, old=off, length=length))
    base = next(s['vm'] for s in segments if s['name'] == '__TEXT')
    for s in segments:
        s['new'] = s['vm'] - base
    def relocate(off):
        if off == 0:
            return 0
        for s in segments:
            if s['old'] <= off < s['old'] + s['length']:
                return s['new'] + off - s['old']
        raise ValueError('Unknown file offset')
    def field(pos):
        value = relocate(struct.unpack_from('<I', data, pos)[0])
        if value >= 2 ** 32:
            raise ValueError('File offset exceeds Mach-O field')
        struct.pack_into('<I', data, pos, value)
    for s in segments:
        p = s['pos']
        struct.pack_into('<Q', data, p + 40, s['new'])
        for i in range(struct.unpack_from('<I', data, p + 64)[0]):
            q = p + 72 + i * 80
            if struct.unpack_from('<I', data, q + 64)[0] & 255 not in (1, 12, 18):
                field(q + 48)
    for p, c, _ in cmds:
        if c in (0x22, 0x80000022):
            for q in range(p + 8, p + 48, 8): field(q)
        elif c == 2:
            field(p + 8); field(p + 16)
        elif c == 0xb:
            for q in (32, 40, 48, 56, 64, 72): field(p + q)
        elif c in (0x1d, 0x1e, 0x26, 0x29, 0x2b, 0x2e, 0x80000033, 0x80000034):
            field(p + 8)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('wb') as f:
        for s in segments:
            f.seek(s['new']); f.write(data[s['old']:s['old'] + s['length']])
        f.truncate(max(s['new'] + s['length'] for s in segments))
    output.chmod(0o755)


def make_variant(raw, device, repository, output):
    """Use the existing six device patch sets, with exact original-byte checks."""
    b = bytearray(raw.read_bytes())
    segs = []
    for p, c, _ in commands(b):
        if c == 0x19:
            vm, _, off, size = struct.unpack_from('<QQQQ', b, p + 24)
            segs.append((b[p + 8:p + 24].rstrip(b'\0'), vm, off, size))
    base = next(vm for n, vm, _, _ in segs if n == b'__TEXT')
    def offset(vm, size):
        for _, start, off, length in segs:
            if start <= vm and vm + size <= start + length:
                return off + vm - start
        raise ValueError('Unmapped patch address')
    h = (repository / 'NootedRed/UserSequoiaSurfaceData.hpp').read_text()
    table = re.search(r'Patch Patches' + device.upper() + r'\[\] = \{(.*?)\};', h, re.S)
    if not table:
        raise ValueError('Unsupported device patch table')
    for addr, old, new, length in re.findall(r'\{(0x[0-9A-F]+)ULL, (\w+), (\w+), (\d+)\}', table[1]):
        original, replacement = array(h, old), array(h, new)
        vm = 0x7ff800000000 + int(addr, 16); p = offset(vm, int(length))
        if len(original) != int(length) or len(replacement) != int(length) or b[p:p + len(original)] != original:
            raise ValueError('Donor Metal bytes differ from the supported software profile')
        b[p:p + len(original)] = replacement
    # Add the helper dependency before choosing the header cave.
    ncmd, size = struct.unpack_from('<II', b, 16); end = 32 + size
    name = b'/usr/lib/NRedMetalCompat.dylib\0'; length = (24 + len(name) + 7) & ~7
    first_section = min(vm for _, vm, _, _ in sections(b)) - base
    if any(b[end:end + length]) or end + length >= first_section:
        raise ValueError('No verified load-command padding')
    command = struct.pack('<IIIIII', 0xc, length, 24, 0, 0x10000, 0x10000) + name
    b[end:end + length] = command + bytes(length - len(command))
    struct.pack_into('<II', b, 16, ncmd + 1, size + length)
    # The data symbol is part of this exact Sequoia profile; ObjC updates its
    # value when the newer MTLIOAccelCommandBuffer superclass grows.
    member_global = 0x7ffb43499768
    if struct.unpack_from('<I', b, offset(member_global, 4))[0] != 0x2e0:
        raise ValueError('Unrecognized command-buffer ivar metadata')
    assembly = (repository / 'tools/submit_guard.s').read_text()
    assembly = assembly.replace('movq 776(%r10), %r10', 'movl 0x12345678(%rip), %edx\n    movq 40(%r10,%rdx), %r10')
    assembly = assembly.replace('movq 776(%rax), %rax', 'movl 0x12345678(%rip), %edx\n    movq 40(%rax,%rdx), %rax')
    with tempfile.TemporaryDirectory() as work:
        source, obj = Path(work) / 'guard.s', Path(work) / 'guard.o'
        source.write_text(assembly)
        subprocess.run(['as', '-arch', 'x86_64', str(source), '-o', str(obj)], check=True)
        blob = obj.read_bytes(); _, _, count, off = next(x for x in sections(blob) if x[0] == '__text')
        payload = bytearray(blob[off:off + count])
    method = base + 0x6c580
    original = array(h.split('namespace Submit {', 1)[1], 'ResolverOriginal0')
    p = offset(method, len(original))
    if b[p:p + len(original)] != original:
        raise ValueError('Unknown submit method')
    cave_off = (end + length + 64 + 15) & ~15; cave = base + cave_off
    if cave_off + len(payload) > first_section or any(b[cave_off:cave_off + len(payload)]):
        raise ValueError('No verified guard padding')
    matches = []
    for i in range(len(payload) - 3):
        if payload[i:i + 4] == struct.pack('<I', 0x12345678):
            payload[i:i + 4] = struct.pack('<i', member_global - (cave + i + 4)); matches.append(i)
    if len(matches) != 2: raise ValueError('Unexpected guard assembly')
    tail = payload.index(bytes.fromhex('49bb8877665544332211'))
    payload[tail:] = b'\xe9' + struct.pack('<i', method + 15 - (cave + tail + 5)) + b'\x90' * (len(payload) - tail - 5)
    b[cave_off:cave_off + len(payload)] = payload
    b[p:p + 15] = b'\xe9' + struct.pack('<i', cave - method - 5) + b'\x90' * 10
    sparse_layout(b, output)


def copy_tree(source, target):
    for f in source.rglob('*'):
        if f.is_symlink():
            raise ValueError('Package inputs must contain regular files: ' + str(f))
        if f.is_file():
            t = target / f.relative_to(source); t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, t); t.chmod(f.stat().st_mode & 0o777)
