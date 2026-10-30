"""在 CPU 上执行两套生成数据中的真实编码参数补丁，不创建 GPU 对象。"""
from pathlib import Path
import subprocess
from refresh_video_encode_data import extract

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/video-main10-patch'


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    for name, index in [('UserVideoDecodeData', 11), ('UserSequoiaVideoData', 9)]:
        text = (ROOT / f'NootedRed/{name}.hpp').read_text()
        code = extract(text, f'Patched{index}')
        native = extract(text, 'ResolverOriginal14')[:138]
        assembly = ''
        for label, data in [('stock', native), ('fixed', code)]:
            assembly += f'.text\n.globl _{label}\n_{label}:\n.byte ' + ','.join(map(str, data)) + '\n'
        (WORK / 'native.s').write_text(assembly)
        source = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
extern "C" void stock(void*,void*,void*);
extern "C" void fixed(void*,void*,void*);
template<class T,size_t N> void put(std::array<uint8_t,N>& b,size_t i,T v){memcpy(b.data()+i,&v,sizeof v);}
std::array<uint32_t,11> params;
void fill(void*,void* job,uint32_t* p){assert(job==(void*)0x1234);p[0]=2;p[9]=7;p[10]=8;}
void capture(void*,unsigned k,const void* p){assert(k==10);memcpy(params.data(),p,44);}
int main(){
 std::array<uint8_t,0x530> enc{};std::array<uint8_t,0x88> tex{};
 std::array<uintptr_t,24> pt{},ct{};pt[0xb0/8]=(uintptr_t)fill;ct[0x70/8]=(uintptr_t)capture;
 uintptr_t proc=(uintptr_t)pt.data();std::array<uint8_t,0x5a8> cmd{};put(cmd,0,(uintptr_t)ct.data());
 put(enc,0x2f8,&proc);put(enc,0x458,cmd.data());put(enc,0x210,1234u);
 for(uint32_t fmt:{0x4e563132u,0x78343230u,0x61726762u})
 for(uint32_t elem:{1u,2u,4u})for(uint32_t width:{16u,1280u,1920u,3840u}){
  uint32_t stride=(width*elem+2047)&~2047u;put(enc,0x9c,fmt);put(enc,0x424,width);put(tex,0x80,stride);put(tex,0x84,elem);
  put(cmd,0x59c,7u);put(cmd,0x5a0,9u);stock(enc.data(),(void*)0x1234,tex.data());auto before=params;
  fixed(enc.data(),(void*)0x1234,tex.data());assert(params[6]==stride/elem&&params[7]==stride/elem/2);
  for(unsigned i=0;i<11;i++)if(i!=6&&i!=7)assert(params[i]==before[i]);
  uint32_t depth,packing;memcpy(&depth,cmd.data()+0x59c,4);memcpy(&packing,cmd.data()+0x5a0,4);
  bool p010=fmt==0x4e563132u&&elem==2;assert(depth==(p010?1u:7u)&&packing==(p010?1u:9u));
 }
}
'''
        (WORK / 'test.cpp').write_text(source)
        exe = WORK / name
        subprocess.run(['xcrun', 'clang++', '-std=c++23', '-Wall', '-Wextra', '-Werror',
                        str(WORK / 'test.cpp'), str(WORK / 'native.s'), '-o', str(exe)], check=True, timeout=60)
        subprocess.run([str(exe)], check=True, timeout=60)
        print('PASS', name, '36 native parameter/format cases')


if __name__ == '__main__':
    main()
