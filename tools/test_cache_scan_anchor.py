"""验证固定字节预筛选与顺序重定位游标的等价性和完整扫描语义。"""
from pathlib import Path
import argparse
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/cache-scan-anchor'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source = r'''
#include <cassert>
#include <cstdio>
#include <vector>
#include <UserCacheResolver.hpp>
using namespace UserCacheResolver;
static constexpr uint64_t base=0x100001;
static std::vector<uint8_t> memory;
static uint64_t fault=0;
static int reader(uint64_t a,void* out,size_t n){
 if(a<base||a-base>memory.size()||n>memory.size()-(a-base)||(fault>=a&&fault-a<n))return 1;
 memcpy(out,memory.data()+a-base,n);return 0;
}
static uint8_t pattern[95];
static bool inspect(unsigned anchorOffset,bool buffered,unsigned& prefixes,uint64_t& found){
 Input input{reader,{base,memory.size()}};uint8_t scan[Input::ScanSize];if(buffered)input.scanBuffer=scan;
 auto prefix=[&](const uint8_t* p){prefixes++;return memcmp(p,pattern,32)==0;};
 auto candidate=[&](uint64_t a){uint8_t bytes[95];return input.read(a,bytes,95)&&memcmp(bytes,pattern,95)==0;};
#if HAS_ANCHOR
 ScanAnchor anchor{anchorOffset,pattern[anchorOffset<32?anchorOffset:0]};
 return scanFunction(input,input.cache,95,32,prefix,candidate,found,0,anchorOffset<32?&anchor:nullptr);
#else
 (void)anchorOffset;return scanFunction(input,input.cache,95,32,prefix,candidate,found);
#endif
}
int main(){
 for(unsigned i=0;i<95;i++)pattern[i]=i+1;
 unsigned cases=0;
 for(bool buffered:{false,true})for(unsigned anchor:{0,7,31})for(unsigned offset:{0,1,992,993,1023,4064,4065,4095,4096,8097}){
  memory.assign(8192,0xcc);memcpy(memory.data()+offset,pattern,95);
  unsigned calls=0;uint64_t found;assert(inspect(anchor,buffered,calls,found)&&found==base+offset);cases++;
  memcpy(memory.data()+(offset<4096?8097:0),pattern,95);calls=0;
  assert(!inspect(anchor,buffered,calls,found));cases++; // 末尾重复也必须拒绝。
 }
 for(bool buffered:{false,true})for(unsigned kind=0;kind<3;kind++){
  memory.assign(8192,0xcc);memcpy(memory.data()+4090,pattern,95);
  if(kind==0)memory[4090+94]^=1;
  if(kind==1)fault=base+6000;
  if(kind==2)fault=base+4090;
  unsigned calls=0;uint64_t found;assert(!inspect(7,buffered,calls,found));fault=0;cases++;
 }
 memory.assign(4*1024*1024,0xcc);unsigned calls[2]{};uint64_t found;
 assert(!inspect(32,true,calls[0],found));assert(!inspect(0,true,calls[1],found));
 printf("prefix checks %u -> %u\n",calls[0],calls[1]);fflush(stdout);assert(calls[1]*100<calls[0]);
#if HAS_ANCHOR
 // 固定字节必须同时属于两个合法前缀，且不能来自被掩掉的重定位字段。
 Prefix a,b;ScanAnchor anchor;
 assert(!a.commonAnchor(b,anchor));
 a.set(4,0x55,false);b.set(4,0x54,false);assert(!a.commonAnchor(b,anchor));
 a.set(7,0x89,false);b.set(7,0x89,false);assert(a.commonAnchor(b,anchor)&&anchor.offset==7&&anchor.value==0x89);
#endif
 // 游标与原逐字节掩码比较等价，包含乱序、重复和重叠引用。
 struct Patch{uint64_t offset;const uint8_t* original;const uint8_t* patched;uint32_t size;};
 uint32_t random=0x12345678;auto next=[&](){random^=random<<13;random^=random>>17;random^=random<<5;return random;};
 for(unsigned sample=0;sample<128;sample++){
  Reference refs[12]{};for(auto& r:refs)r.offset=next()%100;
  if(sample%2==0)for(unsigned i=0;i<12;i++)for(unsigned j=i+1;j<12;j++)if(refs[j].offset<refs[i].offset){auto r=refs[i];refs[i]=refs[j];refs[j]=r;}
  Function fn{0,0,pattern,95,refs,12,refs,12};
  for(unsigned changed=0;changed<95;changed++){
   memcpy(memory.data(),pattern,95);memory[changed]^=1;
   Input input{reader,{base,memory.size()}};
   assert(matches(input,base,fn,0,false,static_cast<const Patch*>(nullptr),nullptr,0)==skipped(changed,refs,12));cases++;
  }
 }
 printf("PASS %u anchor/cursor cases: boundaries, duplicate rejection, byte/fault rejection, masked variants, unordered reference equivalence\n",cases);
}
'''
    file, exe = WORK / 'test.cpp', WORK / 'test'
    file.write_text(source)
    flags = ['-I' + str(args.baseline)] if args.baseline else []
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-DHAS_ANCHOR=' + ('0' if args.baseline else '1'), *flags,
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'), str(file), '-o', str(exe)],
                   check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
