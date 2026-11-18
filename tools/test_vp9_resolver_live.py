"""在各 Darwin 版本的真实只读缓存上验证 VP9、VideoToolbox 候选和视频补丁可共存。"""
import argparse
from pathlib import Path
import struct
import subprocess

from metal_cache import CacheReader

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vp9/resolver-live'
CACHE = Path('/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld')

SOURCE = r'''
#include <cassert>
#include <initializer_list>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>
#include <UserSurfaceSyncData.hpp>
#include <UserVideoDecodeResolver.hpp>
#include <UserVP9DecodeResolver.hpp>
struct Mapping {uint64_t start,end,offset,mask,add;const char* path;int fd=-1;};
static Mapping mappings[]={MAPPINGS};
static constexpr uint64_t base=0x7FF800000000ULL;
static const UserVideoDecodeResolver::RDDPlan* rddOverlay;
static const UserVP9DecodeResolver::Plan* vp9Overlay;
template<class Plan>static void overlay(const Plan* plan,uint64_t address,void* bytes,size_t length){
 if(!plan)return;
 for(unsigned i=0;i<plan->count;++i){
  const auto& patch=plan->patches[i];
  const uint64_t site=base+patch.offset;
  const auto first=std::max(address,site),last=std::min(address+length,site+patch.size);
  if(first<last)memcpy(static_cast<uint8_t*>(bytes)+(first-address),patch.patched+(first-site),last-first);
 }
}
static int reader(uint64_t address,void* bytes,size_t length){
 for(auto&m:mappings)if(m.start<=address&&address<m.end&&length<=m.end-address){
  if(m.fd<0)m.fd=open(m.path,O_RDONLY);if(m.fd<0)return 1;
  if(pread(m.fd,bytes,length,m.offset+address-m.start)!=ssize_t(length))return 1;
  if(length==8&&m.mask){uint64_t value;memcpy(&value,bytes,8);value&=~m.mask;if(value)value+=m.add;memcpy(bytes,&value,8);}
  overlay(rddOverlay,address,bytes,length);
  overlay(vp9Overlay,address,bytes,length);
  return 0;
 }
 return 1;
}
template<class Patch,size_t Sites,size_t Functions>
static void check(const Patch (&reference)[Sites],const UserCacheResolver::Function (&functions)[Functions],
                  const UserCacheResolver::Location (&locations)[Sites]){
 uint8_t scan[UserSharedCache::Input::ScanSize];
 static UserVP9DecodeResolver::Plan plan,combined;
 static UserVideoDecodeResolver::RDDPlan rdd,repeated;
 UserSharedCache::Input native{reader,{}};native.scanBuffer=scan;
 assert(UserVP9DecodeResolver::resolve(native,base,plan,MAJOR)&&plan.count==Sites);
 for(unsigned i=0;i<Sites;++i){
  const auto& p=plan.patches[i];const auto& g=reference[i];
  assert(p.offset==g.offset&&p.size==g.size);
  assert(!memcmp(p.original,g.original,p.size)&&!memcmp(p.patched,g.patched,p.size));
  uint8_t bytes[512];assert(reader(base+p.offset,bytes,p.size)==0);
  assert(!memcmp(bytes,p.original,p.size));
 }

 UserSharedCache::Input rddInput{reader,{}};rddInput.scanBuffer=scan;
 assert(UserVideoDecodeResolver::resolveRDD(rddInput,base,rdd,MAJOR));
 rddOverlay=&rdd;
 UserSharedCache::Input withRDD{reader,{}};withRDD.scanBuffer=scan;
 assert(UserVP9DecodeResolver::resolve(withRDD,base,combined,MAJOR)&&combined.count==Sites);
 for(unsigned i=0;i<Sites;++i){
  const auto& a=plan.patches[i];const auto& b=combined.patches[i];
  assert(a.offset==b.offset&&a.size==b.size);
  assert(!memcmp(a.patched,b.patched,a.size));
 }
 vp9Overlay=&plan;
 UserSharedCache::Input both{reader,{}};both.scanBuffer=scan;
 assert(UserVideoDecodeResolver::resolveRDD(both,base,repeated,MAJOR)&&repeated.count==rdd.count);
 for(unsigned i=0;i<rdd.count;++i)assert(repeated.locations[i].patched);
 for(unsigned f=0;f<Functions;++f){
  assert(UserCacheResolver::matches(both,plan.locations[f].address,functions[f],f,true,reference,locations,Sites));
 }
 for(auto id:{0x15E7U,0x1636U,0x1638U,0x164CU}){
  UserSharedCache::Input in{reader,{}};in.scanBuffer=scan;
  static UserVideoDecodeResolver::Plan video;
  assert(UserVideoDecodeResolver::resolve(in,base,UserVideoDecodeResolver::findDevice(id,MAJOR),video,MAJOR));
 }
 UserSharedCache::Input invalid{reader,{}};invalid.scanBuffer=scan;
 assert(!UserVP9DecodeResolver::resolve(invalid,base,combined,26)&&combined.count==0);
 printf("PASS Darwin %u VP9: %zu sites, %zu functions, %u RDD sites; both overlays preserve video resolver\n",
        MAJOR,Sites,Functions,rdd.count);
}
int main(){
 namespace Data=VP9_DATA;
 check(Data::Patches,Data::ResolverFunctions,Data::ResolverLocations);
}

'''


def mappings(directory):
    with (directory / 'dyld_shared_cache_x86_64h').open('rb') as handle:
        handle.seek(88)
        uuid = handle.read(16).hex()
    cache = CacheReader(directory, uuid)
    out = []
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
                        raise ValueError('slide_info2 required')
                    mask, add = struct.unpack_from('<QQ', info, 24)
            out.append(f'{{0x{start:X}ULL,0x{end:X}ULL,{offset},0x{mask:X}ULL,0x{add:X}ULL,"{handle.name}"}}')
    finally:
        cache.close()
    return uuid, out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--major', type=int, choices=[23, 24, 25], default=25)
    parser.add_argument('--cache', type=Path, default=CACHE)
    args = parser.parse_args()
    work = WORK / str(args.major)
    work.mkdir(parents=True, exist_ok=True)
    uuid, maps = mappings(args.cache.resolve())
    print(f'Darwin {args.major}: {uuid}', flush=True)

    stub = work / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;'
                                    'using UInt64=uint64_t;\n')
    file, executable = work / 'test.cpp', work / 'test'
    profile = {23: 'Sonoma', 24: 'Sequoia', 25: ''}[args.major]
    file.write_text(SOURCE.replace('MAPPINGS', ',\n'.join(maps)).replace('MAJOR', str(args.major))
                    .replace('VP9_DATA', f'UserVP9Decode{profile}Data'))
    subprocess.run(['clang++', '-std=c++23', '-O2', '-I' + str(work / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    str(file), '-o', str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == '__main__':
    main()
