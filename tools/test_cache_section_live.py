"""只读真实共享缓存，验证显示、视频和提交守卫计划，不创建 Metal 设备。"""
import argparse
from pathlib import Path
import struct
import subprocess

from metal_cache import CacheReader

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/cache-section-targets/live'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=Path('/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld'))
    parser.add_argument('--major',type=int,choices=[24,25],default=25)
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    with (args.cache / 'dyld_shared_cache_x86_64h').open('rb') as handle:
        handle.seek(88)
        uuid = handle.read(16).hex()
    cache = CacheReader(args.cache, uuid)
    mappings = []
    try:
        for start, end, offset, handle in cache.mappings:
            handle.seek(312)
            position, count = struct.unpack('<II', handle.read(8))
            handle.seek(position)
            records = handle.read(count * 56)
            mask = add = 0
            for i in range(count):
                address, _, _, slide, size, *_ = struct.unpack_from('<QQQQQQII', records, i * 56)
                if address == start and size:
                    handle.seek(slide)
                    info = handle.read(40)
                    if struct.unpack_from('<I', info)[0] != 2:
                        raise ValueError('需 slide_info2 参考数据')
                    mask, add = struct.unpack_from('<QQ', info, 24)
            mappings.append(f'{{0x{start:X}ULL,0x{end:X}ULL,{offset},0x{mask:X}ULL,0x{add:X}ULL,"{handle.name}"}}')
    finally:
        cache.close()
    source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>
#include <UserSurfaceSyncResolver.hpp>
#include <UserVideoDecodeResolver.hpp>
#include <UserVideoGuardResolver.hpp>
struct Mapping {uint64_t start,end,offset,mask,add;const char* path;int fd=-1;};
static Mapping mappings[]={MAPPINGS};
static int reader(uint64_t address,void* bytes,size_t length){
 for(auto&m:mappings)if(m.start<=address&&address<m.end&&length<=m.end-address){
  if(m.fd<0)m.fd=open(m.path,O_RDONLY);if(m.fd<0)return 1;
  if(pread(m.fd,bytes,length,m.offset+address-m.start)!=ssize_t(length))return 1;
  if(length==8&&m.mask){uint64_t value;memcpy(&value,bytes,8);value&=~m.mask;if(value)value+=m.add;memcpy(bytes,&value,8);}
  return 0;
 }
 return 1;
}
template<class Plan>static void originals(const Plan& plan,unsigned count){
 for(unsigned i=0;i<count;i++){
  auto&p=plan.patches[i];uint8_t bytes[512];assert(p.size>0&&p.size<=sizeof(bytes));
  assert(reader(UserSurfaceSyncData::CacheBase+p.offset,bytes,p.size)==0);
  assert(memcmp(bytes,p.original,p.size)==0);
 }
}
int main(){
 auto base=UserSurfaceSyncData::CacheBase;
 if(MAJOR==25)for(auto&device:UserSurfaceSyncData::DevicePatches){
  UserSurfaceSyncResolver::Plan plan;
  assert(UserSurfaceSyncResolver::resolve(reader,base,device,true,true,plan,true));
  originals(plan,4);printf("PASS real surface device=%x reads=%u\n",device.deviceID,plan.reads);
 }
 for(auto&device:VIDEO_DATA::DevicePatches){
  UserVideoDecodeResolver::Plan plan;
  UserSharedCache::Input input{reader,{}};
  assert(UserVideoDecodeResolver::resolve(input,base,device.patches,plan,MAJOR==24));
  originals(plan,sizeof(plan.patches)/sizeof(plan.patches[0]));
  printf("PASS real video device=%x reads=%u\n",device.deviceID,plan.reads);
  UserVideoGuardResolver::Plan guard;
  assert(UserVideoGuardResolver::resolve(input,base,MAJOR,guard));
  originals(guard,guard.count);
  printf("PASS real video+guard device=%x cumulative reads=%u bytes=%u\n",device.deviceID,input.reads,input.bytes);
 }
 if(MAJOR==25){uint64_t offset=0;assert(UserSurfaceSyncResolver::resolveTahoeSubmit(reader,base,offset));
 printf("PASS real full submit method offset=%llx\n",offset);}
 for(auto&m:mappings)if(m.fd>=0)close(m.fd);
}
'''.replace('MAPPINGS', ',\n'.join(mappings)).replace('MAJOR',str(args.major)).replace('VIDEO_DATA','UserSequoiaVideoData' if args.major==24 else 'UserVideoDecodeData')
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    file, executable = WORK / 'test.cpp', WORK / 'test'
    file.write_text(source)
    subprocess.run(['clang++', '-std=c++23', '-O2', '-I' + str(WORK / 'stubs'),
                    '-I' + str(ROOT / 'NootedRed'), str(file), '-o', str(executable)], check=True, timeout=60)
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
    (WORK / 'result.txt').write_text('cacheUUID=' + uuid + '\n' + result.stdout + result.stderr)
    print(result.stdout, end='')
    print(result.stderr, end='')
    result.check_returncode()


if __name__ == '__main__':
    main()
