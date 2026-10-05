"""只读参考缓存，验证完整扫描预算和真实合并 GOT 的解码，不修改系统文件。"""
from pathlib import Path
import struct
import subprocess
from metal_cache import CacheReader

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/dynamic-cache/reference'


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    cache = CacheReader()
    mappings = []
    try:
        for start, end, offset, handle in cache.mappings:
            handle.seek(312)
            pos, count = struct.unpack('<II', handle.read(8))
            handle.seek(pos)
            records = handle.read(count*56)
            mask = add = 0
            for i in range(count):
                address, _, _, slide, size, *_ = struct.unpack_from('<QQQQQQII', records, i*56)
                if address == start and size:
                    handle.seek(slide)
                    info = handle.read(40)
                    if struct.unpack_from('<I', info)[0] != 2:
                        raise ValueError('需 slide_info2 参考数据')
                    mask, add = struct.unpack_from('<QQ', info, 24)
            mappings.append(f'{{0x{start:X}ULL,0x{end:X}ULL,{offset},0x{mask:X}ULL,0x{add:X}ULL,"{handle.name}"}}')
    finally:
        cache.close()
    stub = WORK/'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub/'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    harness = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>
#include <UserVideoDecodeResolver.hpp>
#include <UserSurfaceSyncResolver.hpp>
struct Mapping{uint64_t start,end,offset,mask,add;const char* path;int fd=-1;};
static Mapping mappings[]={MAPPINGS};
static int reader(uint64_t address,void* bytes,size_t length){
 for(auto& m:mappings)if(m.start<=address&&address<m.end&&length<=m.end-address){
  if(m.fd<0)m.fd=open(m.path,O_RDONLY);if(m.fd<0)return 1;
  if(pread(m.fd,bytes,length,m.offset+address-m.start)!=ssize_t(length))return 1;
  if(length==8&&m.mask){uint64_t raw;memcpy(&raw,bytes,8);raw&=~m.mask;if(raw)raw+=m.add;memcpy(bytes,&raw,8);}
  return 0;
 }
 return 1;
}
int main(){
 for(auto device:UserVideoDecodeData::DevicePatches){
  UserVideoDecodeResolver::Plan plan;
  assert(UserVideoDecodeResolver::resolve(reader,UserVideoDecodeData::CacheBase,device.patches,plan));
  for(unsigned i=0;i<sizeof(plan.patches)/sizeof(plan.patches[0]);i++){
   assert(plan.patches[i].offset==device.patches[i].offset);
   assert(memcmp(plan.patches[i].original,device.patches[i].original,plan.patches[i].size)==0);
   assert(memcmp(plan.patches[i].patched,device.patches[i].patched,plan.patches[i].size)==0);
  }
  printf("PASS real video cache device=%x reads=%u\n",device.deviceID,plan.reads);
 }
 for(auto device:UserSurfaceSyncData::DevicePatches){
  UserSurfaceSyncResolver::Plan plan;
  assert(UserSurfaceSyncResolver::resolve(reader,UserSurfaceSyncData::CacheBase,device,true,true,plan,true));
  for(unsigned i=0;i<4;i++){
   assert(plan.patches[i].offset==device.scratch[i].offset);
   assert(memcmp(plan.patches[i].original,device.scratch[i].original,plan.patches[i].size)==0);
   assert(memcmp(plan.patches[i].patched,device.scratch[i].patched,plan.patches[i].size)==0);
  }
  printf("PASS real compute cache device=%x reads=%u\n",device.deviceID,plan.reads);
 }
 for(auto& m:mappings)if(m.fd>=0)close(m.fd);
}
'''.replace('MAPPINGS', ',\n'.join(mappings))
    source, exe = WORK/'test.cpp', WORK/'test'
    source.write_text(harness)
    subprocess.run(['clang++','-std=c++23','-O2','-I'+str(WORK/'stubs'),'-I'+str(ROOT/'NootedRed'),
                    str(source),'-o',str(exe)],check=True,timeout=60)
    subprocess.run([str(exe)],check=True,timeout=60)


if __name__ == '__main__':
    main()
