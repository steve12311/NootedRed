// 在已接受的立即同步底座上，仅对 0x1638 额外关闭 SRD 表进入 managed/VRAM 分配路径。
.text
.globl _surface_sync_init
_surface_sync_init:
    pushq %rbp
    movq %rsp, %rbp
    pushq %rbx
    pushq %rsi
    movq %rdi, %rbx
    // 合并原生三次 mask/OR；外部调用前的最终结果逐位相同。
    movl 0xa0(%rsi), %eax
    shrl $5, %eax
    movzbl %al, %eax
    xorb $1, %al
    movq (%rdi), %rcx
    movabsq $0xfafffc3000090200, %rdx
    andq %rdx, %rcx
    orq %rax, %rcx
    movabsq $0x050002c2ff70ddfe, %rdx
    orq %rdx, %rcx
    xorl %eax, %eax
    cmpl $3, 0x78(%rsi)
    setae %al
    shll $17, %eax
    orq %rax, %rcx
    movl 0xa0(%rsi), %eax
    shrl %eax
    andl $1, %eax
    shlq $34, %rax
    orq %rax, %rcx
    movq %rcx, (%rdi)
    movl $0x80, 0x14(%rdi)
    // 保留原生 3.0f 常量的 RIP 寻址。
    .byte 0xc4, 0xe2, 0x79, 0x18, 0x05
    .long 0x7ffb10fbfde8 - 0x7ffb10d71a28 - (. - _surface_sync_init + 4)
    vmovlps %xmm0, 0x1c(%rdi)
    movl $0x10000, 0x2c(%rdi)
    movabsq $0x800000008000, %rax
    movq %rax, 0x24(%rdi)
    orb $0xc0, 0x8(%rdi)
    pushq $0x19
    popq %rax
    movl %eax, 0x3c(%rdi)
    movb $0, 0x50(%rdi)
    .byte 0xe8
    .long 0x7ffb10c5422b - 0x7ffb10d71a28 - (. - _surface_sync_init + 4)
    popq %rsi
    cmpl $0x1638, 0xc(%rsi)
    jne Linit_done
    andb $0xfe, 3(%rbx)
    andb $0xbf, 8(%rbx)
Linit_done:
    movq %rbx, %rdi
    popq %rbx
    popq %rbp
    .byte 0xe9
    .long 0x7ffb10c545c6 - 0x7ffb10d71a28 - (. - _surface_sync_init + 4)
    // 另一个原生覆盖入口也保留完整配置读取，然后应用相同设备限制。
    .org 180, 0x90
Loverride:
    pushq %rbp
    movq %rsp, %rbp
    pushq %rbx
    pushq %rsi
    movq %rdi, %rbx
    .byte 0xe8
    .long 0x7ffb10c5422b - 0x7ffb10d71a28 - (. - _surface_sync_init + 4)
    popq %rsi
    cmpl $0x1638, 0xc(%rsi)
    jne Loverride_done
    andb $0xfe, 3(%rbx)
    andb $0xbf, 8(%rbx)
Loverride_done:
    popq %rbx
    popq %rbp
    retq
    .org 215, 0x90
