// NV12 上下文可接收由 GVA 转换得到的 P010 纹理；按实际元素大小修正输入参数。
.text
.globl _payload
_payload:
 pushq %rbx
 xorl %eax,%eax
 pushq %rax
 pushq %rax
 pushq %rax
 pushq %rax
 pushq %rax
 pushq %rax
 movq %rdi,%rbx
 movl 0x210(%rbx),%eax
 movl %eax,4(%rsp)
 movl 0x84(%rdx),%ecx
 cmpl $2,%ecx
 jne Lpitch
 cmpl $0x4e563132,0x9c(%rbx)
 jne Lpitch
 movq 0x458(%rbx),%rax
 movabsq $0x100000001,%r8
 movq %r8,0x59c(%rax)
Lpitch:
 movl 0x80(%rdx),%eax
 xorl %edx,%edx
 divl %ecx
 movl %eax,0x18(%rsp)
 shrl %eax
 movl %eax,0x1c(%rsp)
 movq 0x2f8(%rbx),%rdi
 movq (%rdi),%rax
 movq %rsp,%rdx
 vzeroupper
 callq *0xb0(%rax)
 movq 0x458(%rbx),%rdi
 movq (%rdi),%rax
 pushq $0xa
 popq %rsi
 movq %rsp,%rdx
 callq *0x70(%rax)
 addq $0x30,%rsp
 popq %rbx
 retq
 .org 138,0x90
