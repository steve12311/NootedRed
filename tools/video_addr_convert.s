// 保留 AI 的原有配置；RV 入口由目标设备的 AddrCreate 参数选择。
.text
.globl _payload
_payload:
    cmpl $142, %esi
    je L_rv
    cmpl $141, %esi
    jne L_return
    andl $0xffffffe4, 0x5b10(%rdi)
    orl $1, 0x5b10(%rdi)
    orl $1, 0x5b14(%rdi)
    orl $3, 0x5b18(%rdi)
    leal -1(%rdx), %eax
    cmpl $19, %eax
    jb L_vega10
    orl $12, 0x5b18(%rdi)
    leal -20(%rdx), %eax
    cmpl $20, %eax
    jb L_vega12
    leal -40(%rdx), %eax
    cmpl $215, %eax
    jae L_return
    orl $16, 0x5b10(%rdi)
    jmp L_return
L_vega12:
    orl $8, 0x5b10(%rdi)
    jmp L_return
L_vega10:
    orl $2, 0x5b10(%rdi)
    jmp L_return
L_rv:
    andl $0xffffffe4, 0x5b10(%rdi)
    orl $5, 0x5b10(%rdi)
    andl $0xfffffffe, 0x5b14(%rdi)
    orl $2, 0x5b14(%rdi)
    andl $0xfffffffd, 0x5b18(%rdi)
    orl $13, 0x5b18(%rdi)
L_return:
    movl $8, %eax
    retq
.org 160, 0x90
