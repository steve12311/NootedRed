"""验证共享映射身份证明、已知 RVA 完整核验及热路径回退。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/fvrc-hot-path'


def main():
    stubs = WORK / 'stubs'
    (stubs / 'IOKit').mkdir(parents=True, exist_ok=True)
    (stubs / 'IOKit/IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    (stubs / 'IOKit/IOLib.h').write_text('''#pragma once
#include <IOKit/IOTypes.h>
#include <pthread.h>
#include <stdlib.h>
struct IOLock { pthread_mutex_t mutex; };
inline IOLock* IOLockAlloc(){ auto* lock=static_cast<IOLock*>(malloc(sizeof(IOLock))); if(lock) pthread_mutex_init(&lock->mutex,nullptr); return lock; }
inline void IOLockFree(IOLock* lock){ if(!lock) return; pthread_mutex_destroy(&lock->mutex); free(lock); }
inline void IOLockLock(IOLock* lock){ pthread_mutex_lock(&lock->mutex); }
inline void IOLockUnlock(IOLock* lock){ pthread_mutex_unlock(&lock->mutex); }
''')
    source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <vector>
#include <UserCacheResolver.hpp>
#include <VPF/FastResolution.hpp>
using namespace VPF;

static constexpr uint64_t unslid=0x7ff800000000ULL;
static uint64_t base=0x7ff800200000ULL;
static std::map<uint64_t,std::vector<uint8_t>> blocks;
static void put(uint64_t a,const void* p,size_t n){
 auto i=blocks.upper_bound(a);assert(i!=blocks.begin());--i;
 assert(a-i->first<=i->second.size()&&n<=i->second.size()-(a-i->first));
 memcpy(i->second.data()+(a-i->first),p,n);
}
static void word32(uint64_t a,uint32_t v){put(a,&v,4);}
static void word64(uint64_t a,uint64_t v){put(a,&v,8);}
static uint64_t textStart=0,textEnd=0,textBytes=0;
static int reader(uint64_t a,void* p,size_t n){
 const uint64_t overlapStart=a>textStart?a:textStart,overlapEnd=a+n<textEnd?a+n:textEnd;
 if(overlapStart<overlapEnd)textBytes+=overlapEnd-overlapStart;
 auto i=blocks.upper_bound(a);if(i==blocks.begin())return 1;--i;
 if(a-i->first>i->second.size()||n>i->second.size()-(a-i->first))return 1;
 memcpy(p,i->second.data()+(a-i->first),n);return 0;
}
static void rel(uint64_t a,uint32_t offset,uint64_t target){
 uint8_t b[4];assert(UserSharedCache::setRelative(b,0,a+offset,target));put(a+offset,b,4);
}
static constexpr uint32_t FunctionSize=48,PatchOffset=20,PatchSize=8,TextSize=0x1000;
static uint8_t original[FunctionSize],patched[FunctionSize],patchOriginal[PatchSize],patchPatched[PatchSize];
static UserCacheResolver::Reference refs[1];
static UserCacheResolver::Function functions[1];
struct TestPatch{uint64_t offset;const uint8_t* original;const uint8_t* patched;uint32_t size;};
static UserCacheResolver::Location locations[1]{{0,PatchOffset}};
static TestPatch patches[1]{};

static void pattern(){
 for(uint32_t i=0;i<FunctionSize;i++)original[i]=uint8_t(i*3+1);
 memset(original+8,0,4);
 memcpy(patched,original,FunctionSize);
 patched[PatchOffset]^=0x5a;
 memcpy(patchOriginal,original+PatchOffset,PatchSize);
 memcpy(patchPatched,patched+PatchOffset,PatchSize);
 patches[0]={0,patchOriginal,patchPatched,PatchSize};
 refs[0]={8,0,0x1800,0};
 functions[0]={0,0x1100,original,FunctionSize,refs,1,refs,1};
}
static uint64_t build(uint64_t newBase,uint64_t imageShift=0){
 base=newBase;blocks.clear();
 blocks[base].resize(0x20000);put(base,"dyld_v1 x86_64h",16);memset(blocks[base].data()+88,0x5a,16);
 word32(base+16,0x300);word32(base+20,1);word64(base+224,unslid);word64(base+232,0x40000000);word64(base+240,0x2000000000ULL);
 word32(base+448,0x1000);word32(base+452,1);word64(base+0x300,unslid);word64(base+0x308,0x20000);word32(base+0x31c,1);
 const uint64_t image=base+0x1000000+imageShift;blocks[image].resize(0x80000);
 word64(base+0x1000,image-base+unslid);word32(base+0x1018,0x3000);put(base+0x3000,"/x",3);
 word32(image,0xfeedfacf);word32(image+4,0x01000007);word32(image+12,8);word32(image+16,1);word32(image+20,152);
 auto command=image+32;
 word32(command,0x19);word32(command+4,152);put(command+8,"__TEXT",7);
 word64(command+24,image-base+unslid);word64(command+32,0x80000);word32(command+56,5);word32(command+60,5);word32(command+64,1);
 auto section=command+72;put(section,"__text",7);put(section+16,"__TEXT",7);
 word64(section+32,image+0x1000-base+unslid);word64(section+40,TextSize);
 const uint64_t function=image+0x1000+0x100;
 put(function,original,FunctionSize);rel(function,8,image+0x1800);
 textStart=image+0x1000;textEnd=textStart+TextSize;textBytes=0;
 return image;
}
static int mapCalls=0;
static uint64_t mapTextStart=0,mapTextSize=0,mapObjectId=0x1234;
static uint8_t mapShareMode=5;
static bool mapSplitPrivate=false,mapSubmap=false,mapGap=false,mapWrongObject=false;
static bool probe(void*,uint64_t address,uint32_t depth,VPF::MappingInfo& info){
 mapCalls++;
 if(mapGap){info={address+0x1000,0x1000,depth,0x1234,5,0,false,false};return true;}
 if(mapSubmap&&depth==0){info={mapTextStart,mapTextSize,depth,0,0,0,true,false};return true;}
 const uint64_t start=address<mapTextStart?mapTextStart:address;
 uint64_t length=mapTextStart+mapTextSize-start;
 if(length==0)return false;
 if((mapSplitPrivate||mapWrongObject)&&start==mapTextStart&&length>0x800)length=0x800;
 uint32_t objectId=static_cast<uint32_t>(mapObjectId);
 uint8_t share=mapShareMode;
 if(mapWrongObject&&start>=mapTextStart+0x800)objectId++;
 if(mapSplitPrivate&&start>=mapTextStart+0x800){share=2;objectId++;}
 info={start,length,depth,objectId,share,0,false,false};
 return true;
}
'''
    source += r'''
static void resetMapping(uint64_t textStart,uint64_t textSize=TextSize){
 mapCalls=0;mapTextStart=textStart;mapTextSize=textSize;mapObjectId=0x1234;mapShareMode=5;
 mapSplitPrivate=mapSubmap=mapGap=mapWrongObject=false;
}
static void mapping(){
 const uint64_t image=build(base);(void)image;resetMapping(image+0x1000);
 MappingFacts facts{};
 assert(inspectMapping(probe,nullptr,image+0x1000,TextSize,facts));
 assert(facts.objectId==0x1234&&facts.shareMode==5&&!facts.writable);
 const auto cleanCalls=mapCalls;
 mapSplitPrivate=true;assert(!inspectMapping(probe,nullptr,image+0x1000,TextSize,facts));
 assert(mapCalls>cleanCalls);
 mapSplitPrivate=false;mapWrongObject=true;assert(!inspectMapping(probe,nullptr,image+0x1000,TextSize,facts));
 mapWrongObject=false;mapShareMode=2;assert(!inspectMapping(probe,nullptr,image+0x1000,TextSize,facts));
 mapShareMode=5;mapSubmap=true;assert(inspectMapping(probe,nullptr,image+0x1000,TextSize,facts));
 assert(facts.objectId==0x1234);
 mapSubmap=false;mapGap=true;assert(!inspectMapping(probe,nullptr,image+0x1000,TextSize,facts));
 mapGap=false;assert(!inspectMapping(nullptr,nullptr,image+0x1000,TextSize,facts));
 assert(!inspectMapping(probe,nullptr,0,TextSize,facts));
 assert(!inspectMapping(probe,nullptr,image+0x1000,0,facts));
 printf("PASS mapping: clean shared object, split COW page, object mismatch, writable/private, submap walk, gap and argument rejection\n");
}
static void resolver(){
 pattern();const uint64_t image=build(base);resetMapping(image+0x1000);
 const UserCacheResolver::ImageReference imagesRef[1]={{"/x",3}};
 UserSharedCache::Input input{reader,{}};uint8_t scan[UserSharedCache::Input::ScanSize];input.scanBuffer=scan;
 UserCacheResolver::ImageBounds bounds[1]{};
 assert(UserCacheResolver::resolveImages(input,base,imagesRef,bounds));
 assert(bounds[0].address==image&&bounds[0].text.address==image+0x1000&&bounds[0].text.size==TextSize);
 UserCacheResolver::KnownLocation located[1]{};
 uint8_t originals[16]{},replacements[16]{};
 assert(UserCacheResolver::resolveFunctions(input,base,imagesRef,functions,patches,locations,
        reinterpret_cast<TestPatch*>(nullptr),nullptr,nullptr,0,bounds,false,nullptr,0,nullptr,located));
 (void)originals;(void)replacements;
}
'''
    # resolver 测试需要真实输出缓冲；整体重写该场景，避免占位转换。
    source = source.split('static void resolver(){')[0] + r'''
static void resolver(){
 pattern();const uint64_t image=build(base);resetMapping(image+0x1000);
 const UserCacheResolver::ImageReference imagesRef[1]={{"/x",3}};
 UserSharedCache::Input input{reader,{}};uint8_t scan[UserSharedCache::Input::ScanSize];input.scanBuffer=scan;
 UserCacheResolver::ImageBounds bounds[1]{};
 assert(UserCacheResolver::resolveImages(input,base,imagesRef,bounds));
 assert(bounds[0].address==image&&bounds[0].text.address==image+0x1000&&bounds[0].text.size==TextSize);
 UserCacheResolver::KnownLocation located[1]{};
 uint8_t originals[16]{},replacements[16]{};
 TestPatch output[1]{};
 textBytes=0;
 assert(UserCacheResolver::resolveFunctions(input,base,imagesRef,functions,patches,locations,output,
        originals,replacements,16,bounds,false,nullptr,0,nullptr,located));
 assert(located[0].address==image+0x1100&&!located[0].patched);
 assert(output[0].offset==image+0x1100+20-base);
 const auto coldTextBytes=textBytes;
 // 已知 RVA 只读取完整函数与引用，不进行全域扫描。
 const uint64_t newBase=base+0x100000000ULL;const uint64_t newImage=build(newBase);resetMapping(newImage+0x1000);
 UserSharedCache::Input hot{reader,{}};uint8_t hotScan[UserSharedCache::Input::ScanSize];hot.scanBuffer=hotScan;
 UserCacheResolver::ImageBounds hotBounds[1]{};
 assert(UserCacheResolver::resolveImages(hot,newBase,imagesRef,hotBounds));
 UserCacheResolver::KnownLocation known[1]={{newImage+0x1100,false}};
 uint8_t hotOriginals[16]{},hotReplacements[16]{};TestPatch hotOutput[1]{};
 textBytes=0;
 assert(UserCacheResolver::verifyAtKnownRVA(hot,newBase,imagesRef,functions,patches,locations,known,hotOutput,
        hotOriginals,hotReplacements,16,hotBounds));
 assert(hotOutput[0].offset==newImage+0x1100+20-newBase);
 assert(memcmp(originals,hotOriginals,16)==0&&memcmp(replacements,hotReplacements,16)==0);
 assert(coldTextBytes>=TextSize&&textBytes<512);
 // 错误位置、损坏函数与错误引用目标都必须拒绝；预算耗尽也不能误通过。
 UserCacheResolver::KnownLocation wrong[1]={{newImage+0x1000,false}};
 assert(!UserCacheResolver::verifyAtKnownRVA(hot,newBase,imagesRef,functions,patches,locations,wrong,hotOutput,
        hotOriginals,hotReplacements,16,hotBounds));
 UserCacheResolver::KnownLocation badVariant[1]={{newImage+0x1100,true}};
 assert(!UserCacheResolver::verifyAtKnownRVA(hot,newBase,imagesRef,functions,patches,locations,badVariant,hotOutput,
        hotOriginals,hotReplacements,16,hotBounds));
 const uint64_t functionAddress=newImage+0x1100;
 uint8_t savedByte=0;assert(reader(functionAddress+4,&savedByte,1)==0);
 uint8_t damagedByte=savedByte^1;put(functionAddress+4,&damagedByte,1);
 assert(!UserCacheResolver::verifyAtKnownRVA(hot,newBase,imagesRef,functions,patches,locations,known,hotOutput,
        hotOriginals,hotReplacements,16,hotBounds));
 put(functionAddress+4,&savedByte,1);
 rel(functionAddress,8,newImage+0x1804);
 assert(!UserCacheResolver::verifyAtKnownRVA(hot,newBase,imagesRef,functions,patches,locations,known,hotOutput,
        hotOriginals,hotReplacements,16,hotBounds));
 rel(functionAddress,8,newImage+0x1800);
 UserSharedCache::Input exhausted{reader,{}};exhausted.reads=32768;
 assert(!UserCacheResolver::verifyAtKnownRVA(exhausted,newBase,imagesRef,functions,patches,locations,known,hotOutput,
        hotOriginals,hotReplacements,16,hotBounds));
 printf("PASS resolver: cold scan location, hot known-RVA equivalence, reduced reads, wrong/corrupt/exhausted rejection\n");
}
static void fast(){
 pattern();
 const uint8_t uuid[16]={1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
 ResolutionDiagnostics::setEnabled(true);resolutionCache.init();assert(resolutionCache.ready);
 const UserCacheResolver::ImageReference imagesRef[1]={{"/x",3}};
 const uint32_t functionImages[1]={0};
 const uint64_t firstImage=build(base);resetMapping(firstImage+0x1000);
 UserSharedCache::Input input{reader,{}};uint8_t scan[UserSharedCache::Input::ScanSize];input.scanBuffer=scan;
 UserCacheResolver::ImageBounds bounds[1]{};
 FastResolution fast;fast.begin(uuid,25,0x1234,0,probe,nullptr);
 assert(UserCacheResolver::resolveImages(input,base,imagesRef,bounds));
 assert(fast.observeImages(base,0x40000000,bounds,imagesRef,1)&&fast.mappingClean);
 UserCacheResolver::KnownLocation located[1]{};uint8_t originals[16]{},replacements[16]{};TestPatch output[1]{};
 textBytes=0;
 assert(UserCacheResolver::resolveFunctions(input,base,imagesRef,functions,patches,locations,output,originals,
        replacements,16,bounds,false,nullptr,0,nullptr,located));
 const auto coldTextBytes=textBytes;
 fast.publish(FunctionGroup::VideoDecode,located,functionImages,1);
 assert(resolutionCache.stat(ResolutionStat::Published)==1);
 const uint64_t rva=located[0].address-firstImage;
 // 不同 ASLR 基址、相同相对布局与内核对象身份必须命中。
 const uint64_t secondBase=base+0x40000000ULL;const uint64_t secondImage=build(secondBase);resetMapping(secondImage+0x1000);
 UserSharedCache::Input hot{reader,{}};uint8_t hotScan[UserSharedCache::Input::ScanSize];hot.scanBuffer=hotScan;
 UserCacheResolver::ImageBounds hotBounds[1]{};
 assert(UserCacheResolver::resolveImages(hot,secondBase,imagesRef,hotBounds));
 FastResolution hotFast;hotFast.begin(uuid,25,0x1234,0,probe,nullptr);
 assert(hotFast.observeImages(secondBase,0x40000000,hotBounds,imagesRef,1)&&hotFast.mappingClean);
 UserCacheResolver::KnownLocation known[1]{};
 assert(hotFast.lookup(FunctionGroup::VideoDecode,functionImages,1,known)&&hotFast.hot);
 assert(known[0].address==secondImage+rva&&!known[0].patched);
 uint8_t hotOriginals[16]{},hotReplacements[16]{};TestPatch hotOutput[1]{};
 textBytes=0;
 assert(UserCacheResolver::verifyAtKnownRVA(hot,secondBase,imagesRef,functions,patches,locations,known,hotOutput,
        hotOriginals,hotReplacements,16,hotBounds));
 assert(memcmp(originals,hotOriginals,16)==0&&memcmp(replacements,hotReplacements,16)==0);
 assert(coldTextBytes>=TextSize&&textBytes<512);
 // 私有页、不同对象身份与布局变化都不得命中。
 mapShareMode=2;
 FastResolution dirty;dirty.begin(uuid,25,0x1234,0,probe,nullptr);
 assert(dirty.observeImages(secondBase,0x40000000,hotBounds,imagesRef,1)&&!dirty.mappingClean);
 UserCacheResolver::KnownLocation dirtyLocated[1]={{secondImage+rva,false}};
 dirty.publish(FunctionGroup::VideoGuard,dirtyLocated,functionImages,1);
 assert(!dirty.lookup(FunctionGroup::VideoGuard,functionImages,1,known));
 mapShareMode=5;mapObjectId=0x9999;
 FastResolution other;other.begin(uuid,25,0x1234,0,probe,nullptr);
 assert(other.observeImages(secondBase,0x40000000,hotBounds,imagesRef,1)&&other.mappingClean);
 assert(!other.lookup(FunctionGroup::VideoDecode,functionImages,1,known));
 mapObjectId=0x1234;
 const uint64_t shifted=build(secondBase,0x1000);resetMapping(shifted+0x1000);
 UserSharedCache::Input shiftedInput{reader,{}};UserCacheResolver::ImageBounds shiftedBounds[1]{};
 assert(UserCacheResolver::resolveImages(shiftedInput,secondBase,imagesRef,shiftedBounds));
 FastResolution shiftedFast;shiftedFast.begin(uuid,25,0x1234,0,probe,nullptr);
 assert(shiftedFast.observeImages(secondBase,0x40000000,shiftedBounds,imagesRef,1));
 assert(!shiftedFast.lookup(FunctionGroup::VideoDecode,functionImages,1,known));
 // 伪造的错误 RVA 必须被完整核验拒绝，并允许回退到冷扫描。
 build(secondBase);resetMapping(secondImage+0x1000);
 resolutionCache.init();
 FastResolution forged;forged.begin(uuid,25,0x1234,0,probe,nullptr);
 assert(forged.observeImages(secondBase,0x40000000,hotBounds,imagesRef,1));
 CachedResolution tampered{};
 tampered.key=forged.key(FunctionGroup::DisplaySubmit);tampered.proof=ProofClass::ReusableImmutableIdentity;
 tampered.imageCount=1;tampered.functionCount=1;tampered.resolverRevision=ResolutionVerifierRevision;
 tampered.cacheSize=0x40000000;
 tampered.images[0]={0,0x1234,UInt8(5),secondImage-secondBase,secondImage+0x1000-secondBase,UInt32(TextSize)};
 tampered.functions[0]={0,0,0,0x1000};
 assert(resolutionCache.publish(tampered)==PublishResult::Published);
 UserCacheResolver::KnownLocation forgedKnown[1]{};
 assert(forged.lookup(FunctionGroup::DisplaySubmit,functionImages,1,forgedKnown));
 uint8_t forgedOriginals[16]{},forgedReplacements[16]{};TestPatch forgedOutput[1]{};
 assert(!UserCacheResolver::verifyAtKnownRVA(hot,secondBase,imagesRef,functions,patches,locations,forgedKnown,
        forgedOutput,forgedOriginals,forgedReplacements,16,hotBounds));
 forged.rejectHot();assert(!forged.hot);
 printf("PASS fast: cross-base reuse, dirty/object/layout rejection, forged RVA rejected and cold fallback allowed\n");
}
int main(int argc,char** argv){
 assert(argc==2);const char* scenario=argv[1];
 if(strcmp(scenario,"mapping")==0)mapping();
 else if(strcmp(scenario,"resolver")==0)resolver();
 else if(strcmp(scenario,"fast")==0)fast();
 else return 2;
 return 0;
}
'''
    path, executable = WORK / 'test.cpp', WORK / 'test'
    path.write_text(source)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-Wall', '-Wextra', '-Werror',
                    '-I' + str(stubs), '-I' + str(ROOT / 'NootedRed'),
                    str(path), '-o', str(executable)], check=True, timeout=60)
    for scenario in ('mapping', 'resolver', 'fast'):
        subprocess.run([str(executable), scenario], check=True, timeout=60)


if __name__ == '__main__':
    main()
