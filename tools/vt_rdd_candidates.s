.text
.globl _vtGpuClassifiers
_vtGpuClassifiers:
.macro call_vt target
    .byte 0xe8
    .long _vtGpuClassifiers + \target - . - 4
.endmacro
.macro load_key target
    .byte 0x48, 0x8d, 0x35
    .long _vtGpuClassifiers + \target - . - 4
.endmacro
    load_key IsExternal
# 两个公开分类入口共用原生查询，仍返回自身的分类结果。
Lclassify:
    pushq %rbp
    movq %rsp, %rbp
    pushq %r14
    pushq %rbx
    pushq %rsi
    pushq $0
    movq %rdi, %rbx
    .byte 0x48, 0x8d, 0x3d
    .long _vtGpuClassifiers + Once - . - 4
    load_key Init
    call_vt DispatchOnce
    .byte 0x48, 0x8b, 0x3d
    .long _vtGpuClassifiers + Lock - . - 4
    call_vt Enter
    movq %rbx, %rdi
    call_vt CopyDevice
    movq %rax, %rbx
    movq %rax, %rdi
    movq -24(%rbp), %rsi
    movq %rsp, %rdx
    call_vt ReadBool
    testq %rbx, %rbx
    jz Lunlock
    movq %rbx, %rdi
    call_vt Release
Lunlock:
    .byte 0x48, 0x8b, 0x3d
    .long _vtGpuClassifiers + Lock - . - 4
    call_vt Leave
    popq %rax
    popq %rcx
    popq %rbx
    popq %r14
    popq %rbp
    retq
Lcodec:
    .asciz "AMDVCN2"

.org 0x78, 0x90
LnativeSlotted:
    load_key IsSlotted
    jmp Lclassify

.org 0x81, 0x90
# 候选注册和列表仅在原生槽位检查失败时读取本机 VCN 标记。
LdecoderSlotted:
    pushq %rbp
    movq %rsp, %rbp
    pushq %r14
    pushq %rbx
    movq %rdi, %rbx
    call LnativeSlotted
    testb %al, %al
    jnz LdecoderReturn
    movq %rbx, %rdi
    load_key CodecKey
    call_vt CopyRegistryValue
    testq %rax, %rax
    jz LdecoderReturn
    xchgq %rax, %rbx
    xorl %edi, %edi
    leaq Lcodec(%rip), %rsi
    movl $0x600, %edx
    call_vt CreateString
    xorl %r14d, %r14d
    testq %rax, %rax
    jz LreleaseCodec
    xchgq %rax, %r14
    movq %rbx, %rdi
    movq %r14, %rsi
    call_vt Equal
    movq %r14, %rdi
    movb %al, %r14b
    call_vt Release
LreleaseCodec:
    movq %rbx, %rdi
    call_vt Release
    movzbl %r14b, %eax
LdecoderReturn:
    popq %rbx
    popq %r14
    popq %rbp
    retq
.org 0xf0, 0x90
