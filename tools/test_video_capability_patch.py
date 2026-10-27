"""执行私有页守卫与原生函数的 CPU 回归，不创建 GPU 对象。"""
from pathlib import Path
import hashlib
import json
import subprocess
import re
from video_capability_patch import build_payload, CAPABILITY, AVC_MESSAGE, ENCODE_PICTURE, CONFIGURE, instructions
import build_video_decode_patch as builder

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'build/vcn-capability-test'


def native_function(name,code,callback,work):
    rows=instructions(code,work,builder.compile_payload)
    first=next(off for off,raw,_,_ in rows if raw[0]==0xe8 and len(raw)==5)
    return (f'.globl _{name}\n_{name}:\n.byte '+','.join(map(str,code[:first]))+
            f'\ncallq _{callback}\n.byte '+','.join(map(str,code[first+5:]))+'\n')


def main():
    profiles=json.loads((ROOT/'tools/fixtures/vcn-capability-compat.json').read_text())['profiles']
    for profile in profiles:
        work=WORK/str(profile['major']);work.mkdir(parents=True,exist_ok=True);builder.WORK=work
        for item in profile['functions'].values():assert hashlib.sha256(bytes.fromhex(item['bytes'])).hexdigest()==item['sha256']
        payload,entries,bindings,names,offsets=build_payload(profile,work,builder.compile_payload)
        generated=(ROOT/f'NootedRed/UserVideoGuard{profile["major"]}Data.hpp').read_text()
        def data_array(name):
            body=re.search(r'inline constexpr UInt8 '+name+r'\[\] = \{(.*?)\};',generated,re.S)
            assert body is not None,name
            return bytes(int(value,16) for value in re.findall(r'0x([0-9A-Fa-f]{2})',body[1]))
        assert data_array('Payload')==payload
        entry_body=re.search(r'Entry Entries\[\] = \{(.*?)\};',generated,re.S)[1]
        assert [tuple(map(int,row)) for row in re.findall(r'\{(\d+), (\d+), (\d+)\}',entry_body)]==entries
        binding_body=re.search(r'Binding Bindings\[\] = \{(.*?)\};',generated,re.S)[1]
        assert [(int(a),int(b),int(c),d=='true') for a,b,c,d in re.findall(
            r'\{(\d+), (\d+), (\d+), (true|false)\}',binding_body)]==bindings
        for index,name in enumerate(names):
            expected=profile['functions'][name].get('context',profile['functions'][name])
            code=data_array(f'ResolverOriginal{index}')
            assert code==bytes.fromhex(expected['bytes'])
            assert hashlib.sha256(code).hexdigest()==expected['sha256']
        stock={CAPABILITY:'stockCapability',AVC_MESSAGE:'stockAvc',ENCODE_PICTURE:'stockFrame',CONFIGURE:'stockConfigure'}
        header='#include <sys/mman.h>\n#include <unistd.h>\nstatic void* guardCode;\nstatic void preparePayload(){\n'
        header+='static const uint8_t bytes[]={'+','.join(map(str,payload))+'};\n'
        header+='guardCode=mmap(nullptr,4096,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(guardCode!=MAP_FAILED);memcpy(guardCode,bytes,sizeof(bytes));\n'
        for offset,function,value,reference in bindings:
            symbol=('vcn2vtable' if names[function] not in stock else 'available') if reference else stock[names[function]]
            addition=0 if reference else value
            header+=f'{{uint64_t value=reinterpret_cast<uintptr_t>(&{symbol})+{addition};memcpy(static_cast<uint8_t*>(guardCode)+{offset},&value,8);}}\n'
        header+='assert(mprotect(guardCode,4096,PROT_READ|PROT_EXEC)==0);\n}\n'
        header+=f'extern "C" unsigned fixedCapability(void*a,const void*b){{return reinterpret_cast<decltype(&stockCapability)>(static_cast<uint8_t*>(guardCode)+{offsets["Capability"]})(a,b);}}\n'
        header+=f'extern "C" unsigned fixedAvc(void*a,void*b,void*c,void*d,unsigned e,unsigned f){{return reinterpret_cast<decltype(&stockAvc)>(static_cast<uint8_t*>(guardCode)+{offsets["AVC"]})(a,b,c,d,e,f);}}\n'
        header+=f'extern "C" unsigned fixedFrame(void*a,void*b,void*c,void*d){{return reinterpret_cast<decltype(&stockFrame)>(static_cast<uint8_t*>(guardCode)+{offsets["Frame"]})(a,b,c,d);}}\n'
        header+=f'extern "C" unsigned fixedConfigure(void*a,unsigned b,void*c){{return reinterpret_cast<decltype(&stockConfigure)>(static_cast<uint8_t*>(guardCode)+{offsets["Configure"]})(a,b,c);}}\n'
        (work/'payload.hpp').write_text(header)
        asm='.text\n'
        for source,name,callback in [(CAPABILITY,'stockCapability','available'),(AVC_MESSAGE,'stockAvc','acceptAvc'),(ENCODE_PICTURE,'stockFrame','acceptAvc')]:
            asm+=native_function(name,bytes.fromhex(profile['functions'][source]['bytes']),callback,work)
        asm+='\n.globl _stockConfigure\n_stockConfigure:\n.byte '+','.join(map(str,bytes.fromhex(profile['functions'][CONFIGURE]['bytes'])))+'\n'
        process='__ZN22AvdAvcDecDataProcessor7ProcessEPvP15VAVendorTextureS2_S2_P23_uvd_command_build_info'
        asm+='\n.globl _nativeProcess\n_nativeProcess:\n.byte '+','.join(map(str,bytes.fromhex(profile['functions'][process]['bytes'])))+'\n'
        path=work/'native.s';path.write_text(asm);exe=work/'test'
        subprocess.run(['xcrun','clang++','-std=c++23','-O2','-Wall','-Wextra','-Werror','-I'+str(work),str(ROOT/'tools/fixtures/vcn-capability-native.cpp'),str(path),'-o',str(exe)],check=True,timeout=60)
        subprocess.run([str(exe)],check=True,timeout=60)
        print('Validated private guard Darwin',profile['major'],flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
