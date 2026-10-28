"""VPF 离线 x86-64 证据：反汇编、基本块 CFG 与块级寄存器 liveness。

只用于离线报告，不参与运行时解析或补丁安装。解码依赖本机 `objdump`；无法识别的
控制流、寄存器语义或指令一律拒绝，而不是猜测。这里的 liveness 是块级数据流
（caller-saved 寄存器按 ABI 保守处理），不是完整的编译器级活跃性证明。
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

MAX_INSTRUCTIONS = 65536
MAX_BLOCKS = 16384
MAX_LIVENESS_ROUNDS = 64
FLAGS = 'flags'
CALLER_SAVED = frozenset(
    ('rax', 'rcx', 'rdx', 'rsi', 'rdi', 'r8', 'r9', 'r10', 'r11', 'flags')
    + tuple(f'xmm{i}' for i in range(16))
)
PREFIXES = frozenset(('lock', 'rep', 'repe', 'repne', 'data16', 'data32', 'addr32', 'bnd', 'notrack'))
NOPS = frozenset(('nop', 'nopl', 'nopw', 'nopq', 'endbr64', 'endbr32', 'pause', 'vzeroupper'))
CONDITIONAL = frozenset(
    mnemonic
    for mnemonic in (
        'ja', 'jae', 'jb', 'jbe', 'jc', 'je', 'jg', 'jge', 'jl', 'jle', 'jna', 'jnae', 'jnb', 'jnbe',
        'jnc', 'jne', 'jng', 'jnge', 'jnl', 'jnle', 'jno', 'jnp', 'jns', 'jnz', 'jo', 'jp', 'jpe', 'jpo',
        'js', 'jz', 'jecxz', 'jrcxz', 'loop', 'loope', 'loopne',
    )
)
TERMINATORS = frozenset(('ret', 'retq', 'retf', 'iret', 'iretd', 'iretq', 'ud2', 'hlt'))
MOVE_PREFIXES = ('mov', 'vmov', 'lddqu', 'vlddqu', 'broadcast', 'vbroadcast', 'cvt', 'vcvt')
ARITHMETIC = frozenset(
    (
        'add', 'adc', 'sub', 'sbb', 'and', 'or', 'xor', 'inc', 'dec', 'neg', 'not', 'imul', 'rol', 'ror',
        'rcl', 'rcr', 'shl', 'shr', 'sal', 'sar', 'shld', 'shrd', 'xadd', 'xchg', 'bt', 'bts', 'btr',
        'btc', 'cmpxchg',
    )
)
SSE_WRITE = frozenset(
    (
        'sqrtps', 'sqrtpd', 'sqrtss', 'sqrtsd', 'rcpps', 'rcpss', 'rsqrtps', 'rsqrtss', 'roundps', 'roundpd',
        'roundss', 'roundsd', 'pextrb', 'pextrw', 'pextrd', 'pextrq', 'movmskps', 'movmskpd', 'pmovmskb',
        'pmovzxbw', 'pmovzxbd', 'pmovzxbq', 'pmovzxwd', 'pmovzxwq', 'pmovzxdq', 'pmovsxbw', 'pmovsxbd',
        'pmovsxbq', 'pmovsxwd', 'pmovsxwq', 'pmovsxdq', 'crc32', 'popcnt', 'lzcnt', 'tzcnt', 'bswap',
        'shlx', 'shrx', 'sarx', 'rorx', 'blsi', 'blsr', 'blsmsk', 'bzhi', 'pdep', 'pext', 'andn',
        'bextr',
    )
)
SSE_COMPARE = frozenset(('ptest', 'vptest', 'testps', 'testpd', 'vtestps', 'vtestpd'))
SSE_RW = frozenset(
    (
        'addps', 'addpd', 'addss', 'addsd', 'subps', 'subpd', 'subss', 'subsd', 'mulps', 'mulpd', 'mulss',
        'mulsd', 'divps', 'divpd', 'divss', 'divsd', 'minps', 'minpd', 'minss', 'minsd', 'maxps', 'maxpd',
        'maxss', 'maxsd', 'andps', 'andpd', 'andnps', 'andnpd', 'orps', 'orpd', 'xorps', 'xorpd', 'pxor',
        'por', 'pand', 'pandn', 'paddb', 'paddw', 'paddd', 'paddq', 'psubb', 'psubw', 'psubd', 'psubq',
        'pmullw', 'pmulld', 'pmuludq', 'pcmpeqb', 'pcmpeqw', 'pcmpeqd', 'pcmpgtb', 'pcmpgtw', 'pcmpgtd',
        'psllw', 'pslld', 'psllq', 'psrlw', 'psrld', 'psrlq', 'psraw', 'psrad', 'punpcklbw', 'punpcklwd',
        'punpckldq', 'punpcklqdq', 'punpckhbw', 'punpckhwd', 'punpckhdq', 'punpckhqdq', 'packsswb',
        'packssdw', 'packuswb', 'pinsrb', 'pinsrw', 'pinsrd', 'pinsrq', 'pshufb', 'pshufd', 'pshufhw',
        'pshuflw', 'shufps', 'shufpd', 'blendps', 'blendpd', 'pblendw', 'palignr',
    )
)


class DisassemblyError(ValueError):
    pass


@dataclass(frozen=True)
class Operand:
    kind: str
    register: str = ''
    base: str = ''
    index: str = ''
    scale: int = 1
    displacement: int = 0
    size: int = 0
    immediate: int | None = None
    target: int | None = None
    text: str = ''


@dataclass(frozen=True)
class MemoryAccess:
    direction: str
    size: int
    base: str
    index: str
    scale: int
    displacement: int


@dataclass(frozen=True)
class Instruction:
    offset: int
    raw: bytes
    mnemonic: str
    operands: tuple[Operand, ...]
    text: str
    reads: frozenset[str]
    writes: frozenset[str]
    memory: tuple[MemoryAccess, ...]
    branch_target: int | None
    call_target: int | None
    conditional: bool
    terminator: bool
    indirect_control: bool


@dataclass(frozen=True)
class Block:
    start: int
    instructions: tuple[int, ...]
    successors: tuple[int, ...]
    live_in: frozenset[str]
    live_out: frozenset[str]
    memory: tuple[MemoryAccess, ...]


@dataclass(frozen=True)
class Disassembly:
    instructions: tuple[Instruction, ...]
    blocks: tuple[Block, ...]


_REGISTER_MAP = {
    'rax': 'rax', 'eax': 'rax', 'ax': 'rax', 'al': 'rax', 'ah': 'rax',
    'rbx': 'rbx', 'ebx': 'rbx', 'bx': 'rbx', 'bl': 'rbx', 'bh': 'rbx',
    'rcx': 'rcx', 'ecx': 'rcx', 'cx': 'rcx', 'cl': 'rcx', 'ch': 'rcx',
    'rdx': 'rdx', 'edx': 'rdx', 'dx': 'rdx', 'dl': 'rdx', 'dh': 'rdx',
    'rsi': 'rsi', 'esi': 'rsi', 'si': 'rsi', 'sil': 'rsi',
    'rdi': 'rdi', 'edi': 'rdi', 'di': 'rdi', 'dil': 'rdi',
    'rbp': 'rbp', 'ebp': 'rbp', 'bp': 'rbp', 'bpl': 'rbp',
    'rsp': 'rsp', 'esp': 'rsp', 'sp': 'rsp', 'spl': 'rsp',
    'rip': 'rip', 'eip': 'rip', 'ip': 'rip',
}
_BRANCH = re.compile(r'^(0x[0-9a-f]+|\d+)')
_SIZE = {
    'BYTE': 1, 'WORD': 2, 'DWORD': 4, 'QWORD': 8, 'TBYTE': 10, 'FWORD': 6, 'OWORD': 16,
    'XMMWORD': 16, 'YMMWORD': 32, 'ZMMWORD': 64,
}
_LINE = re.compile(r'^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2}\s+)+)\s*(.*)$')
_OPERAND_SIZE = re.compile(r'^(BYTE|WORD|DWORD|QWORD|TBYTE|FWORD|OWORD|XMMWORD|YMMWORD|ZMMWORD)\s+PTR\s+', re.I)


def canonical_register(name: str) -> str | None:
    lowered = name.strip().lower()
    if lowered in _REGISTER_MAP:
        return _REGISTER_MAP[lowered]
    match = re.fullmatch(r'r(1[0-5]|[89])([dwb])?', lowered)
    if match:
        return f'r{match.group(1)}'
    match = re.fullmatch(r'(xmm|ymm|zmm|mm)(\d+)', lowered)
    if match:
        return f'{match.group(1)}{match.group(2)}'
    return None


def _split_operands(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == '[':
            depth += 1
        elif char == ']':
            depth -= 1
        elif char == ',' and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_memory(text: str, size: int) -> Operand:
    start = text.find('[')
    end = text.find(']', start)
    if start < 0 or end < 0:
        return Operand(kind='other', text=text)
    inner = text[start + 1:end].replace(' ', '').replace('-', '+-')
    base = index = ''
    scale = 1
    displacement = 0
    for term in inner.split('+'):
        if not term:
            continue
        match = re.fullmatch(r'(.+)\*(\d+)', term)
        if match:
            register = canonical_register(match.group(1))
            if register:
                index = register
                scale = int(match.group(2))
                continue
        match = re.fullmatch(r'(\d+)\*(.+)', term)
        if match:
            register = canonical_register(match.group(2))
            if register:
                index = register
                scale = int(match.group(1))
                continue
        register = canonical_register(term)
        if register:
            if register in ('eiz', 'riz'):
                continue
            if not base:
                base = register
            elif not index:
                index = register
            else:
                raise DisassemblyError(f'内存操作数寄存器过多: {text}')
            continue
        if re.fullmatch(r'-?(0x[0-9a-f]+|\d+)', term):
            displacement = int(term, 0)
            continue
        raise DisassemblyError(f'无法解析内存操作数: {text}')
    return Operand(kind='mem', base=base, index=index, scale=scale, displacement=displacement,
                   size=size, text=text)


def _parse_operand(text: str, mnemonic: str) -> Operand:
    text = text.strip()
    size = 0
    match = _OPERAND_SIZE.match(text)
    if match:
        size = _SIZE[match.group(1).upper()]
        text = text[match.end():].strip()
    if '[' in text:
        return _parse_memory(text, size)
    register = canonical_register(text)
    if register:
        return Operand(kind='reg', register=register, size=size, text=text)
    if re.fullmatch(r'-?(0x[0-9a-f]+|\d+)', text):
        return Operand(kind='imm', immediate=int(text, 0), size=size, text=text)
    if mnemonic.startswith('j') or mnemonic in ('call', 'loop', 'loope', 'loopne'):
        match = _BRANCH.match(text)
        if match:
            return Operand(kind='target', target=int(match.group(1), 0), text=text)
    return Operand(kind='other', text=text)


def _register_operands(operand: Operand) -> set[str]:
    result = set()
    if operand.kind == 'reg':
        result.add(operand.register)
    elif operand.kind == 'mem':
        if operand.base:
            result.add(operand.base)
        if operand.index:
            result.add(operand.index)
    return result


def _memory_access(operand: Operand, direction: str) -> MemoryAccess | None:
    if operand.kind != 'mem':
        return None
    return MemoryAccess(direction=direction, size=operand.size, base=operand.base, index=operand.index,
                        scale=operand.scale, displacement=operand.displacement)


def _operand_role(mnemonic: str, index: int, count: int) -> str:
    """返回 read / write / readwrite / none。"""
    if mnemonic in NOPS or mnemonic in PREFIXES:
        return 'none'
    if mnemonic in ('cmp', 'test', 'comiss', 'comisd', 'ucomiss', 'ucomisd', 'bt'):
        return 'read'
    if mnemonic in TERMINATORS or mnemonic in CONDITIONAL:
        return 'none'
    if mnemonic == 'jmp':
        return 'none'
    if mnemonic in ('push',):
        return 'read'
    if mnemonic in ('pop',):
        return 'write' if index == 0 else 'none'
    if mnemonic in ('call',):
        return 'read'
    if mnemonic == 'lea':
        return 'write' if index == 0 else 'none'
    if mnemonic.startswith(MOVE_PREFIXES):
        return 'write' if index == 0 else 'read'
    if mnemonic in SSE_RW:
        return 'readwrite' if index == 0 else 'read'
    if mnemonic in SSE_WRITE:
        return 'write' if index == 0 else 'read'
    if mnemonic in SSE_COMPARE:
        return 'read'
    if mnemonic.startswith('cmp') and mnemonic.endswith(('ps', 'pd', 'ss', 'sd')):
        return 'write' if index == 0 else 'read'
    if mnemonic.startswith('v') and count >= 3:
        return 'write' if index == 0 else 'read'
    if mnemonic in ('div', 'idiv', 'mul', 'imul'):
        return 'read'
    if mnemonic in ARITHMETIC:
        return 'readwrite' if index == 0 else 'read'
    if mnemonic.startswith('cmov'):
        return 'readwrite' if index == 0 else 'read'
    if mnemonic.startswith('set'):
        return 'write' if index == 0 else 'none'
    if mnemonic in ('prefetcht0', 'prefetcht1', 'prefetcht2', 'prefetchnta'):
        return 'read'
    return 'read' if index else 'write'


def _semantics(mnemonic: str, operands: tuple[Operand, ...]) -> tuple[set[str], set[str], list[MemoryAccess]]:
    reads: set[str] = set()
    writes: set[str] = set()
    memory: list[MemoryAccess] = []
    count = len(operands)
    if mnemonic in PREFIXES or mnemonic in NOPS:
        return reads, writes, memory
    if mnemonic in ('ret', 'retq', 'retf'):
        reads.add('rsp')
        writes.add('rsp')
        return reads, writes, memory
    if mnemonic in ('leave',):
        reads.add('rbp')
        writes.update(('rsp', 'rbp'))
        return reads, writes, memory
    if mnemonic == 'call':
        reads.add('rsp')
        writes.add('rsp')
        writes.update(CALLER_SAVED)
        for operand in operands:
            reads.update(_register_operands(operand))
            access = _memory_access(operand, 'read')
            if access:
                memory.append(access)
        return reads, writes, memory
    if mnemonic == 'lea':
        if operands:
            writes.update(_register_operands(operands[0]))
            if len(operands) > 1:
                reads.update(_register_operands(operands[1]))
        return reads, writes, memory
    if mnemonic in CONDITIONAL or mnemonic == 'jmp':
        if mnemonic in CONDITIONAL:
            reads.add(FLAGS)
        return reads, writes, memory
    if mnemonic in TERMINATORS:
        return reads, writes, memory
    if mnemonic in ('push',):
        reads.add('rsp')
        writes.add('rsp')
        for operand in operands:
            reads.update(_register_operands(operand))
            access = _memory_access(operand, 'read')
            if access:
                memory.append(access)
        return reads, writes, memory
    if mnemonic in ('pop',):
        reads.add('rsp')
        writes.add('rsp')
        if operands:
            operand = operands[0]
            if operand.kind == 'reg':
                writes.add(operand.register)
            else:
                reads.update(_register_operands(operand))
                access = _memory_access(operand, 'write')
                if access:
                    memory.append(access)
        return reads, writes, memory
    if mnemonic in ('cdq', 'cqo', 'cwd', 'cbw', 'cwde', 'cdqe'):
        reads.add('rax')
        writes.add('rdx')
        return reads, writes, memory
    if mnemonic in ('div', 'idiv'):
        reads.update(('rax', 'rdx'))
        writes.update(('rax', 'rdx', FLAGS))
        for operand in operands:
            reads.update(_register_operands(operand))
            access = _memory_access(operand, 'read')
            if access:
                memory.append(access)
        return reads, writes, memory
    if mnemonic == 'mul':
        reads.add('rax')
        writes.update(('rax', 'rdx', FLAGS))
        for operand in operands:
            reads.update(_register_operands(operand))
            access = _memory_access(operand, 'read')
            if access:
                memory.append(access)
        return reads, writes, memory
    if mnemonic in ('xchg', 'xadd', 'cmpxchg'):
        for operand in operands:
            reads.update(_register_operands(operand))
            writes.update(_register_operands(operand))
            access = _memory_access(operand, 'readwrite')
            if access:
                memory.append(access)
        if mnemonic == 'cmpxchg':
            reads.add('rax')
            writes.add('rax')
        writes.add(FLAGS)
        return reads, writes, memory
    if mnemonic.startswith('set'):
        if operands:
            writes.update(_register_operands(operands[0]))
        reads.add(FLAGS)
        return reads, writes, memory
    if mnemonic.startswith('cmov'):
        reads.add(FLAGS)
        for index, operand in enumerate(operands):
            if index == 0:
                reads.update(_register_operands(operand))
                writes.update(_register_operands(operand))
            else:
                reads.update(_register_operands(operand))
        return reads, writes, memory
    if mnemonic in ('comiss', 'comisd', 'ucomiss', 'ucomisd', 'test', 'cmp'):
        for operand in operands:
            reads.update(_register_operands(operand))
            access = _memory_access(operand, 'read')
            if access:
                memory.append(access)
        writes.add(FLAGS)
        return reads, writes, memory
    if mnemonic in ('bt', 'bts', 'btr', 'btc'):
        for index, operand in enumerate(operands):
            reads.update(_register_operands(operand))
            if index == 0 and mnemonic != 'bt':
                writes.update(_register_operands(operand))
        writes.add(FLAGS)
        return reads, writes, memory
    if mnemonic in ARITHMETIC or mnemonic.startswith('v') or mnemonic in SSE_RW or mnemonic in SSE_WRITE \
            or mnemonic in SSE_COMPARE or (mnemonic.startswith('cmp') and mnemonic.endswith(('ps', 'pd', 'ss', 'sd'))):
        if mnemonic in ARITHMETIC or mnemonic in SSE_COMPARE or mnemonic in (
                'crc32', 'popcnt', 'lzcnt', 'tzcnt', 'andn', 'blsi', 'blsr', 'blsmsk', 'bextr'):
            writes.add(FLAGS)
        for index, operand in enumerate(operands):
            role = _operand_role(mnemonic, index, count)
            registers = _register_operands(operand)
            if role in ('read', 'readwrite'):
                reads.update(registers)
            if role in ('write', 'readwrite'):
                writes.update(registers)
            direction = 'write' if role == 'write' else ('readwrite' if role == 'readwrite' else 'read')
            access = _memory_access(operand, direction)
            if access:
                memory.append(access)
        return reads, writes, memory
    if mnemonic.startswith(MOVE_PREFIXES) or mnemonic.startswith('set'):
        for index, operand in enumerate(operands):
            role = _operand_role(mnemonic, index, count)
            registers = _register_operands(operand)
            if role in ('read', 'readwrite'):
                reads.update(registers)
            if role in ('write', 'readwrite'):
                writes.update(registers)
            access = _memory_access(operand, 'write' if role == 'write' else 'read')
            if access:
                memory.append(access)
        return reads, writes, memory
    if mnemonic.startswith('cld') or mnemonic.startswith('std'):
        writes.add(FLAGS)
        return reads, writes, memory
    raise DisassemblyError(f'未支持的指令语义: {mnemonic}')


def _macho_text(code: bytes) -> bytes:
    command_size = 72 + 80
    data_offset = (32 + command_size + 15) & ~15
    header = struct.pack('<IIIIIIII', 0xFEEDFACF, 0x01000007, 3, 1, 1, command_size, 0, 0)
    segment_name = b'__TEXT\0'.ljust(16, b'\0')
    section_name = b'__text\0'.ljust(16, b'\0')
    segment = struct.pack('<II16sQQQQIIII', 0x19, command_size, segment_name, 0, len(code), 0, len(code), 7, 5, 1,
                          0)
    section = struct.pack('<16s16sQQIIIIIIII', section_name, segment_name, 0, len(code), data_offset, 4, 0, 0,
                          0x80000400, 0, 0, 0)
    return header + segment + section + b'\0' * (data_offset - 32 - command_size) + code


def _run_objdump(code: bytes) -> str:
    tool = shutil.which('objdump')
    if tool is None:
        raise DisassemblyError('缺少 objdump，无法生成反汇编证据')
    with tempfile.TemporaryDirectory(prefix='vpf-disasm-') as directory:
        path = Path(directory) / 'function.macho'
        path.write_bytes(_macho_text(code))
        result = subprocess.run([tool, '-d', '-M', 'intel', str(path)], capture_output=True, text=True, timeout=30,
                                check=False)
    if result.returncode != 0:
        raise DisassemblyError(result.stderr.strip() or 'objdump 失败')
    return result.stdout


def _parse_objdump(output: str, size: int) -> list[Instruction]:
    instructions: list[Instruction] = []
    for line in output.splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        offset = int(match.group(1), 16)
        raw = bytes.fromhex(match.group(2).strip())
        text = match.group(3).split('##', 1)[0].strip()
        if not text or '(bad)' in text:
            raise DisassemblyError(f'反汇编失败: {line.strip()}')
        parts = text.split(None, 1)
        mnemonic = parts[0].lower()
        operands_text = parts[1] if len(parts) > 1 else ''
        operands = tuple(_parse_operand(item, mnemonic) for item in _split_operands(operands_text))
        if mnemonic in PREFIXES:
            reads, writes, memory = set(), set(), []
        else:
            reads, writes, memory = _semantics(mnemonic, operands)
        branch_target = None
        call_target = None
        indirect = False
        conditional = mnemonic in CONDITIONAL
        terminator = mnemonic in TERMINATORS or mnemonic == 'jmp' or conditional
        if mnemonic in CONDITIONAL or mnemonic in ('jmp', 'call'):
            targets = [operand for operand in operands if operand.kind == 'target']
            if targets:
                target = targets[0].target
                if mnemonic == 'call':
                    call_target = target
                else:
                    branch_target = target
            else:
                indirect = True
        instructions.append(Instruction(offset=offset, raw=raw, mnemonic=mnemonic, operands=operands, text=text,
                                        reads=frozenset(reads), writes=frozenset(writes),
                                        memory=tuple(memory), branch_target=branch_target, call_target=call_target,
                                        conditional=conditional, terminator=terminator,
                                        indirect_control=indirect))
    if not instructions:
        raise DisassemblyError('反汇编为空')
    cursor = 0
    for instruction in instructions:
        if instruction.offset != cursor:
            raise DisassemblyError(f'指令不连续: {instruction.offset:#x} != {cursor:#x}')
        cursor += len(instruction.raw)
    if cursor != size:
        raise DisassemblyError(f'反汇编未覆盖完整函数: {cursor} != {size}')
    if len(instructions) > MAX_INSTRUCTIONS:
        raise DisassemblyError('指令数超限')
    return instructions


def _blocks(instructions: list[Instruction], size: int) -> list[Block]:
    offsets = {instruction.offset: index for index, instruction in enumerate(instructions)}
    leaders = {0}
    for instruction in instructions:
        if instruction.branch_target is not None and 0 <= instruction.branch_target < size:
            if instruction.branch_target not in offsets:
                raise DisassemblyError('分支目标不在指令边界')
            leaders.add(instruction.branch_target)
        if instruction.terminator:
            next_offset = instruction.offset + len(instruction.raw)
            if next_offset < size:
                leaders.add(next_offset)
    sorted_leaders = sorted(leaders)
    if len(sorted_leaders) > MAX_BLOCKS:
        raise DisassemblyError('基本块数超限')
    block_of_offset = {offset: index for index, offset in enumerate(sorted_leaders)}
    blocks: list[Block] = []
    for index, start in enumerate(sorted_leaders):
        end = sorted_leaders[index + 1] if index + 1 < len(sorted_leaders) else size
        indices = tuple(i for i, instruction in enumerate(instructions) if start <= instruction.offset < end)
        if not indices:
            raise DisassemblyError('空基本块')
        blocks.append(Block(start=start, instructions=indices, successors=(), live_in=frozenset(),
                            live_out=frozenset(), memory=()))
    rebuilt: list[Block] = []
    for index, block in enumerate(blocks):
        last = instructions[block.instructions[-1]]
        successors: list[int] = []
        if last.conditional and last.branch_target is not None:
            if last.branch_target in block_of_offset:
                successors.append(block_of_offset[last.branch_target])
            fallthrough = last.offset + len(last.raw)
            if fallthrough < size:
                successors.append(block_of_offset[fallthrough])
        elif last.mnemonic == 'jmp':
            if last.branch_target is None:
                raise DisassemblyError('间接跳转没有静态目标')
            if last.branch_target in block_of_offset:
                successors.append(block_of_offset[last.branch_target])
        elif last.terminator:
            pass
        else:
            fallthrough = last.offset + len(last.raw)
            if fallthrough < size:
                successors.append(block_of_offset[fallthrough])
        memory = tuple(item for instruction_index in block.instructions for item in instructions[
            instruction_index].memory)
        rebuilt.append(Block(start=block.start, instructions=block.instructions, successors=tuple(successors),
                             live_in=frozenset(), live_out=frozenset(), memory=memory))
    for instruction in instructions:
        if instruction.indirect_control:
            raise DisassemblyError('存在间接控制流')
    return rebuilt


def _liveness(blocks: list[Block], instructions: list[Instruction]) -> list[Block]:
    use = [set() for _ in blocks]
    define = [set() for _ in blocks]
    for index, block in enumerate(blocks):
        for instruction_index in block.instructions:
            instruction = instructions[instruction_index]
            use[index].update(register for register in instruction.reads if register not in define[index])
            define[index].update(instruction.writes)
    live_in = [frozenset() for _ in blocks]
    live_out = [frozenset() for _ in blocks]
    for _ in range(MAX_LIVENESS_ROUNDS):
        changed = False
        for index in range(len(blocks) - 1, -1, -1):
            outgoing = frozenset().union(*(live_in[successor] for successor in blocks[index].successors))
            incoming = frozenset(use[index]) | (outgoing - frozenset(define[index]))
            if outgoing != live_out[index]:
                live_out[index] = outgoing
                changed = True
            if incoming != live_in[index]:
                live_in[index] = incoming
                changed = True
        if not changed:
            return [
                Block(start=block.start, instructions=block.instructions, successors=block.successors,
                      live_in=live_in[index], live_out=live_out[index], memory=block.memory)
                for index, block in enumerate(blocks)
            ]
    raise DisassemblyError('liveness 未收敛')


def disassemble(code: bytes) -> Disassembly:
    if not isinstance(code, bytes) or not 1 <= len(code) <= 65536:
        raise DisassemblyError('函数长度非法')
    instructions = _parse_objdump(_run_objdump(code), len(code))
    blocks = _liveness(_blocks(instructions, len(code)), instructions)
    return Disassembly(instructions=tuple(instructions), blocks=tuple(blocks))
