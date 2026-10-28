"""VPF：离线语义变体证据；默认只报告，不产生可安装 Manifest。

严格约束：
- typed reference 的偏移/大小/类型/目标/映像必须一致；
- patch site 的位置、大小和原始字节必须一致；
- 函数必须能被反汇编，且没有间接控制流；
- 基本块 CFG、指令序列（允许一致的寄存器重命名）、块级 liveness 和数据访问必须一致。

这不替代运行时唯一性/设备/ABI 校验，也不是完整的编译器语义证明。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import x86_disasm as x86

USER_LIMIT = 0x0000800000000000
MAX_FUNCTION_SIZE = 64 * 1024
MIN_FUNCTION_SIZE = 32
ACCEPTED = 'ACCEPTED'
REJECTED = 'REJECTED'
PROOF_EXACT = 'ExactBytes'
PROOF_CFG = 'DisassemblyCFGBlockLiveness'


@dataclass(frozen=True)
class TypedReference:
    offset: int
    size: int = 4
    kind: str = 'rel32'
    target: int = 0
    image: int = 0
    indirect: int = 0

    def __post_init__(self):
        if self.offset < 0 or self.size not in (4, 8):
            raise ValueError(f'非法引用范围: {self!r}')
        if self.target < 0 or self.target >= USER_LIMIT:
            raise ValueError(f'引用目标越界: {self!r}')
        if self.image < 0 or self.indirect not in (0, 1, 2):
            raise ValueError(f'非法引用类型: {self!r}')
        if not self.kind:
            raise ValueError('引用缺少类型')


@dataclass(frozen=True)
class PatchConstraint:
    offset: int
    size: int
    original: bytes

    def __post_init__(self):
        if self.offset < 0 or self.size <= 0 or len(self.original) != self.size:
            raise ValueError(f'非法补丁约束: {self!r}')


@dataclass(frozen=True)
class FunctionVariant:
    name: str
    image: str
    code: bytes
    references: tuple[TypedReference, ...] = ()
    patches: tuple[PatchConstraint, ...] = ()
    darwin: int | None = None
    device_id: int | None = None
    source_build: str = ''
    source_cache_uuid: str = ''
    evidence: str = ''

    def __post_init__(self):
        if not self.name or not self.image:
            raise ValueError('变体缺少名称或映像')
        if not isinstance(self.code, bytes) or not MIN_FUNCTION_SIZE <= len(self.code) <= MAX_FUNCTION_SIZE:
            raise ValueError(f'{self.name}: 函数长度越界')
        previous = -1
        for reference in self.references:
            if reference.offset + reference.size > len(self.code) or reference.offset < previous:
                raise ValueError(f'{self.name}: 引用越界或重叠')
            previous = reference.offset + reference.size
        previous = -1
        for patch in self.patches:
            if patch.offset < previous or patch.offset + patch.size > len(self.code):
                raise ValueError(f'{self.name}: 补丁越界或重叠')
            if self.code[patch.offset:patch.offset + patch.size] != patch.original:
                raise ValueError(f'{self.name}: 补丁原始字节与函数不一致')
            previous = patch.offset + patch.size


@dataclass(frozen=True)
class Verdict:
    status: str
    reason: str
    proof_level: str
    exact: bool
    candidate: bool
    emitted_manifests: int
    normalized_digest: str
    register_mapping: tuple[tuple[str, str], ...] = ()
    first_difference: int | None = None


def _reference_layout(reference: TypedReference) -> tuple[int, int, str, int, int]:
    return reference.offset, reference.size, reference.kind, reference.image, reference.indirect


def _patch_layout(patch: PatchConstraint) -> tuple[int, int, bytes]:
    return patch.offset, patch.size, patch.original


def _masked(code: bytes, references: tuple[TypedReference, ...]) -> bytes:
    masked = bytearray(code)
    for reference in references:
        masked[reference.offset:reference.offset + reference.size] = b'\0' * reference.size
    return bytes(masked)


def _first_difference(reference: bytes, candidate: bytes) -> int | None:
    for index, (left, right) in enumerate(zip(reference, candidate)):
        if left != right:
            return index
    return None


def normalized_digest(variant: FunctionVariant) -> str:
    payload = {
        'image': variant.image,
        'code': _masked(variant.code, variant.references).hex(),
        'references': [
            {
                'offset': reference.offset,
                'size': reference.size,
                'kind': reference.kind,
                'target': reference.target,
                'image': reference.image,
                'indirect': reference.indirect,
            }
            for reference in variant.references
        ],
        'patches': [
            {'offset': patch.offset, 'size': patch.size, 'original': patch.original.hex()}
            for patch in variant.patches
        ],
        'proofLevel': PROOF_CFG,
    }
    text = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _validate_reference_fields(disassembly: x86.Disassembly,
                               references: tuple[TypedReference, ...]) -> str | None:
    for reference in references:
        containing = None
        for instruction in disassembly.instructions:
            if instruction.offset <= reference.offset < instruction.offset + len(instruction.raw):
                containing = instruction
                break
        if containing is None or reference.offset + reference.size > containing.offset + len(containing.raw):
            return 'reference_alignment'
    return None


def _operand_signature(operand: x86.Operand) -> tuple:
    if operand.kind == 'reg':
        return ('reg', operand.register)
    if operand.kind == 'mem':
        return ('mem', operand.base, operand.index, operand.scale, operand.displacement, operand.size)
    if operand.kind == 'imm':
        return ('imm', operand.immediate)
    if operand.kind == 'target':
        return ('target', operand.target)
    return ('other', operand.text)


def _compare_semantics(reference: FunctionVariant, candidate: FunctionVariant,
                       reference_disassembly: x86.Disassembly,
                       candidate_disassembly: x86.Disassembly) -> tuple[bool, str, dict[str, str]]:
    if len(reference_disassembly.blocks) != len(candidate_disassembly.blocks):
        return False, 'control_flow', {}
    mapping: dict[str, str] = {'flags': 'flags'}
    reverse: dict[str, str] = {'flags': 'flags'}

    def register_class(name: str) -> str:
        if name == 'flags':
            return 'flags'
        if name.startswith('xmm'):
            return 'xmm'
        if name.startswith('ymm'):
            return 'ymm'
        if name.startswith('zmm'):
            return 'zmm'
        if name.startswith('mm'):
            return 'mm'
        return 'gpr'

    def map_register(left: str, right: str) -> bool:
        if left in ('rsp', 'rip') or right in ('rsp', 'rip'):
            return left == right
        if register_class(left) != register_class(right):
            return False
        if left in mapping:
            return mapping[left] == right
        if right in reverse:
            return False
        mapping[left] = right
        reverse[right] = left
        return True

    def map_target(target: int, disassembly: x86.Disassembly) -> int | str:
        for index, block in enumerate(disassembly.blocks):
            if block.start == target:
                return index
        return f'0x{target:x}'

    for ref_block, cand_block in zip(reference_disassembly.blocks, candidate_disassembly.blocks):
        if len(ref_block.instructions) != len(cand_block.instructions):
            return False, 'instruction_count', {}
        if ref_block.successors != cand_block.successors:
            return False, 'control_flow', {}
        for ref_index, cand_index in zip(ref_block.instructions, cand_block.instructions):
            left = reference_disassembly.instructions[ref_index]
            right = candidate_disassembly.instructions[cand_index]
            if left.mnemonic != right.mnemonic:
                return False, 'instruction_sequence', {}
            if len(left.operands) != len(right.operands):
                return False, 'operand_count', {}
            for left_operand, right_operand in zip(left.operands, right.operands):
                left_signature = _operand_signature(left_operand)
                right_signature = _operand_signature(right_operand)
                if left_operand.kind != right_operand.kind:
                    return False, 'operand_kind', {}
                if left_operand.kind == 'reg':
                    if not map_register(left_operand.register, right_operand.register):
                        return False, 'register_mapping', {}
                elif left_operand.kind == 'mem':
                    if (left_operand.scale, left_operand.displacement, left_operand.size) != (
                            right_operand.scale, right_operand.displacement, right_operand.size):
                        return False, 'data_access', {}
                    for left_reg, right_reg in ((left_operand.base, right_operand.base),
                                                (left_operand.index, right_operand.index)):
                        if bool(left_reg) != bool(right_reg):
                            return False, 'register_mapping', {}
                        if left_reg and not map_register(left_reg, right_reg):
                            return False, 'register_mapping', {}
                elif left_operand.kind == 'imm':
                    if left_operand.immediate != right_operand.immediate:
                        return False, 'immediate', {}
                elif left_operand.kind == 'target':
                    if map_target(left_operand.target, reference_disassembly) != map_target(
                            right_operand.target, candidate_disassembly):
                        return False, 'control_flow', {}
                elif left_signature != right_signature:
                    return False, 'operand_text', {}
        ref_memory = [(item.direction, item.size, item.base, item.index, item.scale, item.displacement)
                      for item in ref_block.memory]
        cand_memory = [(item.direction, item.size, item.base, item.index, item.scale, item.displacement)
                       for item in cand_block.memory]
        if len(ref_memory) != len(cand_memory):
            return False, 'data_access', {}
        for left_item, right_item in zip(ref_memory, cand_memory):
            left_mapped = (left_item[0], left_item[1], mapping.get(left_item[2], left_item[2]),
                           mapping.get(left_item[3], left_item[3]), left_item[4], left_item[5])
            if left_mapped != right_item:
                return False, 'data_access', {}
        if frozenset(mapping.get(register, register) for register in ref_block.live_in) != cand_block.live_in:
            return False, 'register_liveness', {}
        if frozenset(mapping.get(register, register) for register in ref_block.live_out) != cand_block.live_out:
            return False, 'register_liveness', {}
    return True, 'semantic_equivalent', mapping


def compare_variants(reference: FunctionVariant, candidate: FunctionVariant) -> Verdict:
    if len(reference.code) != len(candidate.code):
        return Verdict(REJECTED, 'size', PROOF_CFG, False, False, 0, '')
    if tuple(_reference_layout(item) for item in reference.references) != tuple(
            _reference_layout(item) for item in candidate.references):
        return Verdict(REJECTED, 'reference_layout', PROOF_CFG, False, False, 0, '')
    if tuple(item.target for item in reference.references) != tuple(item.target for item in candidate.references):
        return Verdict(REJECTED, 'reference_target', PROOF_CFG, False, False, 0, '')
    if tuple(_patch_layout(item) for item in reference.patches) != tuple(
            _patch_layout(item) for item in candidate.patches):
        return Verdict(REJECTED, 'patch_site', PROOF_CFG, False, False, 0, '')

    exact = reference.code == candidate.code
    if exact:
        return Verdict(ACCEPTED, 'exact', PROOF_EXACT, True, False, 0, normalized_digest(reference))
    try:
        reference_disassembly = x86.disassemble(reference.code)
        candidate_disassembly = x86.disassemble(candidate.code)
    except x86.DisassemblyError as error:
        return Verdict(REJECTED, f'disassembly:{error}', PROOF_CFG, False, False, 0, '')
    alignment = _validate_reference_fields(reference_disassembly, reference.references)
    if alignment is None:
        alignment = _validate_reference_fields(candidate_disassembly, candidate.references)
    if alignment is not None:
        return Verdict(REJECTED, alignment, PROOF_CFG, False, False, 0, '')
    accepted, reason, mapping = _compare_semantics(reference, candidate, reference_disassembly,
                                                   candidate_disassembly)
    if not accepted:
        return Verdict(REJECTED, reason, PROOF_CFG, False, False, 0, '')
    return Verdict(ACCEPTED, reason, PROOF_CFG, False, True, 0, normalized_digest(reference),
                   tuple(sorted(mapping.items())))


def select_unique(reference: FunctionVariant, candidates: list[FunctionVariant]) -> tuple[FunctionVariant | None, str]:
    accepted = []
    for candidate in candidates:
        verdict = compare_variants(reference, candidate)
        if verdict.status == ACCEPTED:
            accepted.append(candidate)
    if not accepted:
        return None, 'no_match'
    if len(accepted) > 1:
        return None, 'ambiguous'
    return accepted[0], 'selected'


def variant_to_json(variant: FunctionVariant) -> dict:
    return {
        'name': variant.name,
        'image': variant.image,
        'bytes': variant.code.hex(),
        'references': [
            {
                'offset': reference.offset,
                'size': reference.size,
                'kind': reference.kind,
                'target': reference.target,
                'image': reference.image,
                'indirect': reference.indirect,
            }
            for reference in variant.references
        ],
        'patches': [
            {'offset': patch.offset, 'size': patch.size, 'original': patch.original.hex()}
            for patch in variant.patches
        ],
        'darwin': variant.darwin,
        'deviceID': variant.device_id,
        'sourceBuild': variant.source_build,
        'sourceCacheUUID': variant.source_cache_uuid,
        'evidence': variant.evidence,
        'normalizedDigest': normalized_digest(variant),
    }


def load_variants(path: Path) -> list[FunctionVariant]:
    data = json.loads(path.read_text())
    if data.get('kind') not in (None, 'vpf.semantic-variants'):
        raise ValueError(f'{path}: 未知 kind')
    variants = []
    for item in data.get('variants', []):
        references = tuple(TypedReference(**reference) for reference in item.get('references', []))
        patches = tuple(
            PatchConstraint(offset=patch['offset'], size=patch['size'], original=bytes.fromhex(patch['original']))
            for patch in item.get('patches', [])
        )
        variants.append(
            FunctionVariant(
                name=item['name'],
                image=item['image'],
                code=bytes.fromhex(item['bytes']),
                references=references,
                patches=patches,
                darwin=item.get('darwin'),
                device_id=item.get('deviceID'),
                source_build=item.get('sourceBuild', ''),
                source_cache_uuid=item.get('sourceCacheUUID', ''),
                evidence=item.get('evidence', ''),
            )
        )
    return variants
