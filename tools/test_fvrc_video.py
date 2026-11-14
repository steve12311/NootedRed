"""验证视频 Decode 解析在真实生成数据上的跨 ASLR 复用与回退。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/fvrc-video'


def main():
    stubs = WORK / 'stubs'
    (stubs / 'IOKit').mkdir(parents=True, exist_ok=True)
    (stubs / 'IOKit/IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    (stubs / 'IOKit/IOLocks.h').write_text('#pragma once\n#include <mutex>\nstruct IOLock{std::mutex m;};\ninline void IOLockLock(IOLock*l){l->m.lock();}\ninline void IOLockUnlock(IOLock*l){l->m.unlock();}\n')
    (stubs / 'IOKit/IOLib.h').write_text('#pragma once\n#include <IOKit/IOLocks.h>\ninline IOLock* IOLockAlloc(){return new IOLock;}\n')
    fixture = (ROOT / 'tools/fixtures/user-cache-dynamic.hpp').read_text()
    fixture = fixture.replace('static constexpr uint64_t base=0x7ff800200000ULL,unslid=base-0x200000;',
                              'static uint64_t base=0x7ff800200000ULL,unslid=0x7ff800000000ULL;static uint64_t TEXTBYTES=0;')
    fixture = fixture.replace('word64(base+240,0x200000);', 'word64(base+240,0x2000000000ULL);')
    fixture = fixture.replace('blocks.clear();protections.clear();',
                              'blocks.clear();unslid=base-0x200000;TEXTBYTES=0;protections.clear();')
    fixture = fixture.replace(
        'static int reader(uint64_t a,void* p,size_t n){',
        'static int reader(uint64_t a,void* p,size_t n){\n'
        ' for(size_t ti=0;ti<imageAddresses.size();ti++){const uint64_t ts=imageAddresses[ti]+0x1000,te=ts+0x6f000;'
        'const uint64_t os=a>ts?a:ts,oe=a+n<te?a+n:te;if(os<oe)TEXTBYTES+=oe-os;}')
    source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <UserVideoDecodeResolver.hpp>
#include <UserRejectedCaches.hpp>
static std::map<std::string,unsigned> properties;
''' + fixture + r'''
static bool mappingDirty=false,mappingWrongObject=false;
static unsigned dirtyImage=0;
static bool probe(void*,uint64_t address,uint32_t depth,VPF::MappingInfo& info){
 for(size_t i=0;i<imageAddresses.size();i++){
  const uint64_t start=imageAddresses[i]+0x1000,end=start+0x6f000;
  if(address>=start&&address<end){
   const uint8_t share=(mappingDirty&&i==dirtyImage)?2:5;
   const uint32_t objectId=0x1234U+static_cast<uint32_t>(i)+(mappingWrongObject&&i==1?1U:0U);
   info={address,end-address,depth,objectId,share,0,false,false};
   return true;
  }
 }
 return false;
}
static void resetMapping(){mappingDirty=mappingWrongObject=false;dirtyImage=0;}
static const uint8_t uuid[16]={1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
static constexpr uint32_t device=0x15e7;
static VPF::FastResolution beginFast(){VPF::FastResolution fast;
 fast.begin(uuid,25,VPF::resolutionProfile(device,0),0,probe,nullptr);return fast;}
static void video(){
 using namespace UserVideoDecodeData;
 const auto* devicePatches=UserVideoDecodeData::findDevice(device);assert(devicePatches);
 cache(ResolverImages,ResolverFunctions,true);resetMapping();TEXTBYTES=0;
 UserVideoDecodeResolver::Plan first{};UserSharedCache::Input coldInput{reader,{}};
 auto fastFirst=beginFast();
 assert(UserVideoDecodeResolver::resolve(coldInput,base,devicePatches,first,25,&fastFirst));
 assert(!fastFirst.hot&&TEXTBYTES>0x100000);const auto coldText=TEXTBYTES;
 assert(VPF::resolutionCache.stat(VPF::ResolutionStat::Published)>=1);
 base+=0x40000000ULL;cache(ResolverImages,ResolverFunctions,true);resetMapping();TEXTBYTES=0;
 UserVideoDecodeResolver::Plan second{};UserSharedCache::Input hotInput{reader,{}};
 auto fastSecond=beginFast();
 assert(UserVideoDecodeResolver::resolve(hotInput,base,devicePatches,second,25,&fastSecond));
 assert(fastSecond.hot&&TEXTBYTES*4<coldText);
 for(unsigned i=0;i<UserVideoDecodeResolver::PatchCount;i++){
  assert(first.patches[i].offset==second.patches[i].offset&&first.patches[i].size==second.patches[i].size);
 }
 base+=0x40000000ULL;cache(ResolverImages,ResolverFunctions,true);resetMapping();mappingDirty=true;dirtyImage=1;
 UserVideoDecodeResolver::Plan dirty{};UserSharedCache::Input dirtyInput{reader,{}};
 auto fastDirty=beginFast();
 assert(UserVideoDecodeResolver::resolve(dirtyInput,base,devicePatches,dirty,25,&fastDirty));
 assert(!fastDirty.hot);
 base+=0x40000000ULL;cache(ResolverImages,ResolverFunctions,true);resetMapping();mappingWrongObject=true;
 UserVideoDecodeResolver::Plan other{};UserSharedCache::Input otherInput{reader,{}};
 auto fastOther=beginFast();
 assert(UserVideoDecodeResolver::resolve(otherInput,base,devicePatches,other,25,&fastOther));
 assert(!fastOther.hot);
 for(unsigned i=0;i<UserVideoDecodeResolver::PatchCount;i++){
  assert(first.patches[i].offset==dirty.patches[i].offset&&first.patches[i].offset==other.patches[i].offset);
 }
 printf("PASS fvrc-video-decode: cross-ASLR reuse, read reduction, dirty/object fallback, relative patch equality\n");
}
static void rejectedCaches(){
 using namespace UserVideoDecodeData;
 const auto* patches=UserVideoDecodeData::findDevice(device);assert(patches);
 cache(ResolverImages,ResolverFunctions,true);
 UserRejectedCaches::Table rejected;assert(rejected.init());
 failedRead=base+0x1000;
 for(unsigned i=0;i<2;i++){
  UserVideoDecodeResolver::Plan plan{};UserSharedCache::Input input{reader,{}};
  assert(!UserVideoDecodeResolver::resolve(input,base,patches,plan,25)&&input.failed);
  rejected.rejectMissingImages(uuid,input);
 }
 failedRead=0;
 for(unsigned i=0;i<64;i++)assert(!rejected.shouldSkip(uuid));
 UserVideoDecodeResolver::Plan recovered{};UserSharedCache::Input input{reader,{}};
 assert(UserVideoDecodeResolver::resolve(input,base,patches,recovered,25));
 // 损坏函数匹配字节，镜像表仍完整：这种进程相关失败也不缓存。
 for(unsigned f=0;f<UserVideoDecodeResolver::FunctionCount;f++){const uint8_t bad=0;put(functionAddresses[f],&bad,1);}
 for(unsigned i=0;i<2;i++){
  UserVideoDecodeResolver::Plan plan{};UserSharedCache::Input bad{reader,{}};
  assert(!UserVideoDecodeResolver::resolve(bad,base,patches,plan,25));
  assert(bad.tableRead&&!bad.failed);
  rejected.rejectMissingImages(uuid,bad);
 }
 assert(!rejected.shouldSkip(uuid));
 // 完整扫描之后确认目标镜像缺失：才允许记忆同一 UUID。
 cache(ResolverImages,ResolverFunctions,true);put(base+0x3000,"!",1);
 for(unsigned i=0;i<2;i++){
  UserVideoDecodeResolver::Plan plan{};UserSharedCache::Input missing{reader,{}};
  assert(!UserVideoDecodeResolver::resolve(missing,base,patches,plan,25));
  assert(missing.tableRead&&!missing.failed&&missing.imageAddresses[0]==0);
  rejected.rejectMissingImages(uuid,missing);
 }
 assert(rejected.shouldSkip(uuid));
 puts("PASS rejected cache integration: read recovery and code mismatch never skip, complete missing-image table confirms rejection");
}
int main(){
 VPF::ResolutionDiagnostics::setEnabled(true);VPF::resolutionCache.init();assert(VPF::resolutionCache.ready);
 video();
 rejectedCaches();
 return 0;
}
'''
    path, executable = WORK / 'test.cpp', WORK / 'test'
    path.write_text(source)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-I' + str(stubs), '-I' + str(ROOT / 'NootedRed'), '-I' + str(ROOT / 'tools/fixtures'),
                    str(path), '-o', str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=120)


if __name__ == '__main__':
    main()
