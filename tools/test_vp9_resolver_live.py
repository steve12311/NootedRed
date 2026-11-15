"""VP9 V3: runs the kext's UserVP9DecodeResolver against this Mac's real shared cache (read only).

Checks that every site resolves at the generated offset with the original bytes and the generated
replacement, that with the VP9 bytes applied the video resolver still matches and every VP9 function
matches its patched variant, and that the common Darwin 23/24/25 dispatch accepts this verified ABI.
"""
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
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>
#include <UserSurfaceSyncData.hpp>
#include <UserVideoDecodeResolver.hpp>
#include <UserVP9DecodeResolver.hpp>
struct Mapping {uint64_t start,end,offset,mask,add;const char* path;int fd=-1;};
static Mapping mappings[]={MAPPINGS};
static bool overlay=false;
static int reader(uint64_t address,void* bytes,size_t length){
 for(auto&m:mappings)if(m.start<=address&&address<m.end&&length<=m.end-address){
  if(m.fd<0)m.fd=open(m.path,O_RDONLY);if(m.fd<0)return 1;
  if(pread(m.fd,bytes,length,m.offset+address-m.start)!=ssize_t(length))return 1;
  if(length==8&&m.mask){uint64_t value;memcpy(&value,bytes,8);value&=~m.mask;if(value)value+=m.add;memcpy(bytes,&value,8);}
  if(overlay)for(auto&p:UserVP9DecodeData::Patches){
   auto site=0x7FF800000000ULL+p.offset;
   for(size_t i=0;i<p.size;i++)if(site+i>=address&&site+i<address+length)
    static_cast<uint8_t*>(bytes)[site+i-address]=p.patched[i];
  }
  return 0;
 }
 return 1;
}
static void check(const char* label){
 const auto base=0x7FF800000000ULL;
 uint8_t scan[UserSharedCache::Input::ScanSize];
 for(auto&device:UserVideoDecodeData::DevicePatches){
  static UserVideoDecodeResolver::Plan video;memset(&video,0,sizeof(video));
  UserSharedCache::Input in{reader,{}};in.scanBuffer=scan;
  assert(UserVideoDecodeResolver::resolve(in,base,device.patches,video,25));
 }
 printf("PASS video resolver %s\n",label);
 static UserVP9DecodeResolver::Plan plan;memset(&plan,0,sizeof(plan));
 UserSharedCache::Input input{reader,{}};input.scanBuffer=scan;
 if(overlay){
  // A process whose pages already carry the backend: every function matches its patched variant byte for
  // byte, but the original GOT fields under the region are gone, so a full re-resolution is refused by
  // design. The kext resolves once per boot from clean pages and only compares site bytes afterwards.
  assert(UserCacheResolver::resolveImages(input,base,UserVP9DecodeData::ResolverImages,plan.images));
  constexpr unsigned count=sizeof(UserVP9DecodeData::ResolverFunctions)/sizeof(UserVP9DecodeData::ResolverFunctions[0]);
  for(unsigned f=0;f<count;f++){
   const auto&fn=UserVP9DecodeData::ResolverFunctions[f];const auto address=plan.images[fn.image].address+fn.rva;
   assert(UserCacheResolver::matches(input,address,fn,f,true,UserVP9DecodeData::Patches,UserVP9DecodeData::ResolverLocations,UserVP9DecodeResolver::PatchCount));
   // A hook that only retargets a call changes nothing but a rel32 field, which the byte match skips (it is
   // checked as a reference); every other function must stop matching its original variant.
   bool referenceOnly=true;
   for(unsigned p=0;p<UserVP9DecodeResolver::PatchCount;p++){
    const auto&loc=UserVP9DecodeData::ResolverLocations[p];if(loc.function!=f)continue;
    const auto&g=UserVP9DecodeData::Patches[p];
    for(size_t i=0;i<g.size;i++){
     if(g.original[i]==g.patched[i])continue;
     bool covered=false;const auto at=loc.offset+i;
     for(unsigned r=0;r<fn.originalCount;r++)covered|=fn.originalRefs[r].offset<=at&&at<fn.originalRefs[r].offset+4;
     referenceOnly&=covered;
    }
   }
   if(referenceOnly)continue;
   assert(!UserCacheResolver::matches(input,address,fn,f,false,UserVP9DecodeData::Patches,UserVP9DecodeData::ResolverLocations,UserVP9DecodeResolver::PatchCount));
  }
  assert(!UserVP9DecodeResolver::resolve(input,base,plan));
  printf("PASS vp9 %s: %u functions match the patched variant, re-resolution refused\n",label,count);
  return;
 }
 assert(UserVP9DecodeResolver::resolve(input,base,plan));
 for(unsigned i=0;i<UserVP9DecodeResolver::PatchCount;i++){
  const auto&p=plan.patches[i];const auto&g=UserVP9DecodeData::Patches[i];
  assert(p.offset==g.offset&&p.size==g.size);
  assert(!memcmp(p.original,g.original,p.size)&&!memcmp(p.patched,g.patched,p.size));
  uint8_t bytes[512];assert(reader(base+p.offset,bytes,p.size)==0);
  assert(!memcmp(bytes,p.original,p.size));
 }
 printf("PASS vp9 %s: %u sites, reads=%u bytes=%u\n",label,UserVP9DecodeResolver::PatchCount,input.reads,input.bytes);
}
int main(){
 check("original");
 overlay=true;check("vp9-applied");overlay=false;
 static UserVP9DecodeResolver::Plan plan;uint8_t scan[UserSharedCache::Input::ScanSize];
 for(unsigned major : {23U,24U,25U}) {
  UserSharedCache::Input input{reader,{}};input.scanBuffer=scan;
  assert(UserVP9DecodeResolver::resolve(input,0x7FF800000000ULL,plan,major));
 }
 UserSharedCache::Input invalid{reader,{}};invalid.scanBuffer=scan;
 assert(!UserVP9DecodeResolver::resolve(invalid,0x7FF800000000ULL,plan,26));
 printf("PASS dynamic profile admitted on Darwin 23/24/25; unsupported major refused\n");
}
'''


def mappings():
    with (CACHE / 'dyld_shared_cache_x86_64h').open('rb') as handle:
        handle.seek(88)
        uuid = handle.read(16).hex()
    cache = CacheReader(CACHE, uuid)
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
    WORK.mkdir(parents=True, exist_ok=True)
    uuid, maps = mappings()

    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;'
                                    'using UInt64=uint64_t;\n')
    file, executable = WORK / 'test.cpp', WORK / 'test'
    file.write_text(SOURCE.replace('MAPPINGS', ',\n'.join(maps)))
    subprocess.run(['clang++', '-std=c++23', '-O2', '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    str(file), '-o', str(executable)], check=True, timeout=120)
    subprocess.run([str(executable)], check=True, timeout=120)


if __name__ == '__main__':
    main()
