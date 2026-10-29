"""验证扫描字节查找的首命中、非对齐边界及页外不读取语义。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/cache-scan-byte'


def main():
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source = r'''
#include <cassert>
#include <cstdio>
#include <vector>
#include <sys/mman.h>
#include <unistd.h>
#include <UserCacheResolver.hpp>
using UserCacheResolver::findScanByte;
int main(){
 unsigned cases=0;
 assert(findScanByte(nullptr,0,0)==nullptr);
 // 覆盖所有值、起始对齐、尾长；哨兵在范围外不能命中。
 for(unsigned value=0;value<256;value++)for(unsigned align=0;align<8;align++)for(unsigned size=0;size<=65;size++){
  std::vector<UInt8> bytes(align+size+8,UInt8(value^0xff));
  auto* start=bytes.data()+align;start[size]=value;
  assert(findScanByte(start,value,size)==nullptr);cases++;
  for(unsigned position=0;position<size;position++){
   start[position]=value;
   assert(findScanByte(start,value,size)==start+position);cases++;
   start[position]=value^0xff;
  }
 }
 // 含多个命中的随机数据与 libc 对照，特别覆盖零字节检测的相邻借位。
 UInt32 random=0x12345678;auto next=[&](){random^=random<<13;random^=random>>17;random^=random<<5;return random;};
 UInt8 bytes[520];
 for(unsigned trial=0;trial<20000;trial++){
  for(auto& b:bytes)b=next();
  unsigned align=next()%8,size=next()%513;UInt8 value=next();
  assert(findScanByte(bytes+align,value,size)==memchr(bytes+align,value,size));cases++;
 }
 UInt8 borrow[]={0x80,0x81,0x81,0x81,0x81,0x81,0x81,0x81};
 assert(findScanByte(borrow,0x80,8)==borrow);
 // 对齐页尾后是不可读页面，任何向上取整的读取都会失败。
 const auto page=sysconf(_SC_PAGESIZE);
 auto* mapping=static_cast<UInt8*>(mmap(nullptr,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0));
 assert(mapping!=MAP_FAILED&&mprotect(mapping+page,page,PROT_NONE)==0);
 memset(mapping,0xcc,page);
 for(unsigned size=0;size<=128;size++){
  auto* start=mapping+page-size;
  assert(findScanByte(start,0x80,size)==nullptr);cases++;
  if(size){start[size-1]=0x80;assert(findScanByte(start,0x80,size)==start+size-1);start[size-1]=0xcc;cases++;}
 }
 assert(munmap(mapping,page*2)==0);
 printf("PASS %u byte-scan cases: all values, alignments, first match, tail bounds and protected page\n",cases);
}
'''
    path, executable = WORK / 'test.cpp', WORK / 'test'
    path.write_text(source)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-Wall', '-Wextra', '-Werror',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    str(path), '-o', str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == '__main__':
    main()
