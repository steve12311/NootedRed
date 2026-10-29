"""生成 VCN2 私有 RX 页；原生函数只重放入口，不改写消息打包代码。"""
import re
import struct
import subprocess

CAPABILITY = '__ZN13AvdCapability19checkCapabilityListEPK21AVD_DriverContextInfo'
AVC_MESSAGE = '__ZN22AvdAvcDecDataProcessor22SetDecodeMessageFieldsEPvP15VAVendorTextureP23_uvd_command_build_infojj'
ENCODE_PICTURE = '__ZN12VAUveEncoder13EncodePictureEPvP15VAVendorTextureS0_'
CONFIGURE = '__ZN12VAUveEncoder9configureEjPv'
CTOR = '__ZN11Vcn2EncoderC1E12VAEncodeType'
VCN2_VTABLE = '__ZTV11Vcn2Encoder'


def instructions(native, work, compile_payload):
    compile_payload('.text\n.globl _native\n_native:\n.byte '+','.join(map(str,native))+'\n','capability-disasm')
    out=subprocess.run(['xcrun','llvm-objdump','-d',str(work/'capability-disasm.o')],capture_output=True,text=True,check=True,timeout=60).stdout
    rows=[]
    for line in out.splitlines():
        m=re.match(r'\s*([0-9a-f]+):\s*((?:[0-9a-f]{2}\s+)+)\s*(\S+)\s*(.*)',line)
        if m: rows.append((int(m[1],16),bytes.fromhex(m[2]),m[3],m[4].split('##')[0].strip()))
    return rows


def build_payload(profile, work, compile_payload):
    names=[CAPABILITY,AVC_MESSAGE,ENCODE_PICTURE,CTOR,CONFIGURE]
    native={n:bytes.fromhex(profile['functions'][n]['bytes']) for n in names}
    cap_exit=native[CAPABILITY].index(bytes.fromhex('5b415c415e415f5dc3'))
    bindings=[];exports={};entries=[]
    out='.text\n.globl _payload\n_payload:\n.ascii "NRedVCN2"\n.long 1\n.long 0\n.long 0\n.long '+str(profile['major'])+'\n.quad 0\n'

    def mark(name,label): exports[name]=label
    def raw(code): return '.byte '+','.join(map(str,code))+'\n'
    def transfer(function,value,reference=False,call=False):
        index=len(bindings);bindings.append((names.index(function),value,reference))
        label=f'LBinding{index}';mark(f'Binding{index}',label)
        return f'.byte 0x49,0xbb\n{label}:\n.quad 0\n'+('callq *%r11\n' if call else 'jmpq *%r11\n')
    def replay(function,size):
        rows=instructions(native[function],work,compile_payload)
        if size not in [off for off,_,_,_ in rows]:raise ValueError('重放入口不在指令边界')
        if any(op.startswith(('j','call')) or '%rip' in args for off,_,op,args in rows if off<size):
            raise ValueError('重放入口包含控制流或 RIP 引用')
        return raw(native[function][:size])

    # 先执行原生刷新；只在其成功后检查申请表，失败仍走原生错误码。
    mark('Capability','LCapability')
    entries.append((0,14,'Capability'))
    out+='LCapability:\n'+replay(CAPABILITY,0x11)+transfer(CAPABILITY,0x12,True,True)
    out+=('testl %eax, %eax\njne LAvailabilityError\nmovq 0xb2e0(%rbx), %r11\ncallq LTargetGPU\njnc LCapabilityContinue\n'
          'testq %r14, %r14\nje LCapabilityError\nmovl (%r14), %ecx\ntestl %ecx, %ecx\nje LCapabilityContinue\n'
          'movq 8(%r14), %r9\ntestq %r9, %r9\nje LCapabilityError\nxorl %edx, %edx\nLCapabilityLoop:\n'
          'movq (%r9,%rdx,8), %r10\ntestq %r10, %r10\nje LCapabilityError\ncmpl $32, (%r10)\njne LCapabilityNext\n'
          'movq 8(%r10), %rax\ncmpq $0x20, %rax\nje LAVCSize\ncmpq $0x800, %rax\nje LAVCSize\n'
          'cmpq $0x1000, %rax\njne LCapabilityError\nmovl $130, %eax\njmp LCheckCapabilitySize\n'
          'LAVCSize:\nmovl $128, %eax\nLCheckCapabilitySize:\ncallq LDimensions\njc LCapabilityError\n'
          'LCapabilityNext:\nincl %edx\ncmpl %ecx, %edx\njb LCapabilityLoop\nLCapabilityContinue:\n')
    out+=transfer(CAPABILITY,0x25)
    out+='LAvailabilityError:\nmovl $1, %eax\njmp LCapabilityReturn\nLCapabilityError:\nmovl $5, %eax\nLCapabilityReturn:\n'
    out+=transfer(CAPABILITY,cap_exit)

    mark('AVC','LAVC')
    entries.append((1,17,'AVC'))
    out+=('LAVC:\nmovq 0x18(%rdi), %r11\ntestq %r11, %r11\nje LAVCError\n'
          'movq 0x460(%r11), %r11\ntestq %r11, %r11\nje LAVCError\nmovq (%r11), %r11\n'
          'testq %r11, %r11\nje LAVCError\ncallq LTargetGPU\njnc LAVCContinue\ntestq %rsi, %rsi\nje LAVCError\nmovq 0x260(%rsi), %rax\ntestq %rax, %rax\nje LAVCError\n'
          'cmpl $66, 4(%rax)\nje LAVCProfile\ncmpl $77, 4(%rax)\nje LAVCProfile\ncmpl $100, 4(%rax)\njne LAVCError\n'
          'LAVCProfile:\ncmpq $1, 0xc(%rax)\njne LAVCError\ncmpq $0, 0x14(%rax)\njne LAVCError\n')
    out+='LAVCContinue:\n'+replay(AVC_MESSAGE,17)+transfer(AVC_MESSAGE,17)
    out+='LAVCError:\nmovl $5, %eax\nretq\n'

    mark('Frame','LFrame')
    entries.append((2,20,'Frame'))
    out+='LFrame:\npushq %rcx\n'
    # 通过原生构造函数的实际 RIP 目标绑定虚表，不能固定其缓存 RVA。
    bindings.append((3,0x14,True));i=len(bindings)-1;mark(f'Binding{i}',f'LBinding{i}')
    out+=f'.byte 0x49,0xbb\nLBinding{i}:\n.quad 0\n'
    out+=('cmpq (%rdi), %r11\njne LFrameContinue\nmovq 0x58(%rdi), %r11\ntestq %r11, %r11\nje LFrameError\n'
          'movq 0x460(%r11), %r11\ntestq %r11, %r11\nje LFrameError\nmovq (%r11), %r11\n'
          'testq %r11, %r11\nje LFrameError\ncallq LTargetGPU\njnc LFrameContinue\ntestq %rdx, %rdx\nje LFrameError\n'
          'movzbl 0x52c(%rdi), %eax\nleal 128(,%rax,2), %eax\nleaq 0x40(%rdi), %r10\ncallq LDimensions\njc LFrameError\n'
          'movl 0x60(%rdi), %ecx\ncmpl 0x424(%rdi), %ecx\nja LFrameError\ncmpl 0x78(%rdx), %ecx\nja LFrameError\n'
          'movl 0x64(%rdi), %ecx\ncmpl 0x428(%rdi), %ecx\nja LFrameError\ncmpl 0x7c(%rdx), %ecx\nja LFrameError\n'
          'movl 0x70(%rdx), %ecx\ncmpl 0x78(%rdx), %ecx\nja LFrameError\nmovl 0x74(%rdx), %ecx\ncmpl 0x7c(%rdx), %ecx\nja LFrameError\n'
          'leaq 0x404(%rdi), %r10\nmovl $128, %eax\ncallq LDimensions\njc LFrameError\n'
          'leaq 0x50(%rdx), %r10\nmovl $128, %eax\ncallq LDimensions\njc LFrameError\n'
          'leaq 0x58(%rdx), %r10\nmovl $128, %eax\ncallq LDimensions\njc LFrameError\n')
    # 可见尺寸已核验且不会溢出；只允许覆盖其末尾宏块内的已分配填充区域。
    out+=('movl 0x70(%rdx), %ecx\naddl $15, %ecx\nandl $-16, %ecx\ncmpl %ecx, 0x60(%rdi)\nja LFrameError\n'
          'movl 0x74(%rdx), %ecx\naddl $15, %ecx\nandl $-16, %ecx\ncmpl %ecx, 0x64(%rdi)\nja LFrameError\n'
          'LFrameContinue:\npopq %rcx\n')
    out+=replay(ENCODE_PICTURE,20)+transfer(ENCODE_PICTURE,20)
    out+='LFrameError:\npopq %rcx\nmovl $5, %eax\nretq\n'

    mark('Configure','LConfigure');entries.append((4,16,'Configure'))
    bindings.append((3,0x14,True));i=len(bindings)-1;mark(f'Binding{i}',f'LBinding{i}')
    out+=f'LConfigure:\n.byte 0x49,0xbb\nLBinding{i}:\n.quad 0\n'
    out+=('cmpq (%rdi), %r11\njne LConfigureContinue\nmovq 0x58(%rdi), %r11\ntestq %r11, %r11\nje LConfigureError\n'
          'movq 0x460(%r11), %r11\ntestq %r11, %r11\nje LConfigureError\nmovq (%r11), %r11\n'
          'testq %r11, %r11\nje LConfigureError\ncallq LTargetGPU\njnc LConfigureContinue\n'
          'movzbl 0x52c(%rdi), %eax\nleal 128(,%rax,2), %eax\nleaq 0x40(%rdi), %r10\ncallq LDimensions\njc LConfigureError\n'
          'LConfigureContinue:\n')
    out+=replay(CONFIGURE,16)+transfer(CONFIGURE,16)
    out+='LConfigureError:\nmovl $5, %eax\nretq\n'

    out+=('LDimensions:\ncmpl %eax, 0x20(%r10)\njb LInvalidDimensions\ncmpl $4096, 0x20(%r10)\nja LInvalidDimensions\n'
          'cmpl $128, 0x24(%r10)\njb LInvalidDimensions\ncmpl $2304, 0x24(%r10)\nja LInvalidDimensions\n'
          'movl 0x20(%r10), %eax\norl 0x24(%r10), %eax\ntestb $1, %al\njne LInvalidDimensions\nretq\nLInvalidDimensions:\nstc\nretq\n'
          'LTargetGPU:\ntestq %r11, %r11\nje LOtherGPU\nmovzwl 4(%r11), %eax\ncmpl $0x15e7, %eax\nje LTargetYes\n'
          'subl $0x1636, %eax\ncmpl $22, %eax\nja LOtherGPU\nmovl $0x400005, %r11d\nbtl %eax, %r11d\nretq\n'
          'LTargetYes:\nstc\nretq\nLOtherGPU:\nclc\nretq\n')
    for name,label in exports.items():out+=f'.globl _NRed{name}Offset\n.set _NRed{name}Offset, {label} - _payload\n'
    payload=bytearray(compile_payload(out,'video-guard'))
    reloc=subprocess.run(['xcrun','llvm-objdump','-r',str(work/'video-guard.o')],capture_output=True,text=True,check=True,timeout=60).stdout
    if 'X86_64_RELOC' in reloc:raise ValueError('守卫包含未解析对象重定位')
    symbols=subprocess.run(['xcrun','nm','-a',str(work/'video-guard.o')],capture_output=True,text=True,check=True,timeout=60).stdout
    offsets={n:int(v,16) for v,n in re.findall(r'^([0-9a-fA-F]+) [Aa] _NRed(\w+)Offset$',symbols,re.M)}
    if len(payload)>4096:raise ValueError('守卫页超过单页预算')
    struct.pack_into('<I',payload,16,len(payload))
    return bytes(payload),[(f,size,offsets[name]) for f,size,name in entries],[(offsets[f'Binding{i}'],*v) for i,v in enumerate(bindings)],names,offsets
