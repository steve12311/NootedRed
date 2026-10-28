"""验证跨进程解析缓存的身份键、发布/快照并发、冲突禁用与固定容量。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/fvrc-registry'


def main():
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    (stub / 'IOLib.h').write_text('''#pragma once
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
#include <atomic>
#include <thread>
#include <vector>
#include <VPF/VerifiedResolutionCache.hpp>
using namespace VPF;
static ResolutionKey makeKey(uint32_t group,uint32_t profile,uint64_t seed){
 ResolutionKey key{};for(unsigned i=0;i<16;i++)key.uuid[i]=uint8_t((seed>>((i%8)*8))^(i*7+1));
 key.darwinMajor=25;key.arch=ResolutionArchX86_64h;key.manifestRevision=ResolutionVerifierRevision;
 key.profileId=profile;key.functionGroupId=group;key.imageSet={seed,seed*0x9E3779B97F4A7C15ULL^(seed<<1)};return key;
}
static CachedResolution makeValue(const ResolutionKey& key,uint64_t rva=0x11000,ProofClass proof=ProofClass::ReusableImmutableIdentity){
 CachedResolution value{};value.key=key;value.proof=proof;value.imageCount=1;value.functionCount=1;
 value.resolverRevision=ResolutionVerifierRevision;value.cacheSize=0x10000000;value.proofFingerprint=rva;
 value.images[0]={0,proof==ProofClass::ReusableImmutableIdentity?0x1234U:0U,
                  UInt8(proof==ProofClass::ReusableImmutableIdentity?5:0),0x100000,0x200000,0x10000};
 value.functions[0]={0,0,0,rva};return value;
}
static void basic(){
 const auto key=makeKey(uint32_t(FunctionGroup::VideoDecode),0,7);
 assert(key.valid());
 auto changed=key;changed.uuid[3]^=1;assert(changed!=key);changed=key;changed.darwinMajor=24;assert(changed!=key);
 changed=key;changed.arch=2;assert(changed!=key);changed=key;changed.manifestRevision=9;assert(changed!=key);
 changed=key;changed.profileId=3;assert(changed!=key);changed=key;changed.functionGroupId=uint32_t(FunctionGroup::VideoGuard);assert(changed!=key);
 changed=key;changed.imageSet.high^=1;assert(changed!=key);
 auto bad=key;bad.functionGroupId=0;assert(!bad.valid());bad.functionGroupId=uint32_t(FunctionGroup::Count);assert(!bad.valid());
 // 缓存相对布局相同则跨 ASLR 的身份一致；路径或范围变化必须改变身份。
 const char pathA[]="/System/Library/Extensions/A",pathB[]="/System/Library/Extensions/B";
 ImageLayout layout[2]={{pathA,sizeof(pathA),0x100000,0x200000,0x10000,0,0},{pathB,sizeof(pathB),0x300000,0x400000,0x20000,0,0}};
 const auto identity=imageSetIdentity(layout,2,0x10000000);
 assert(identity.low&&identity.high);
 assert(imageSetIdentity(layout,2,0x10000000)==identity);
 auto altered=layout[1];altered.relativeStart+=0x1000;assert(imageSetIdentity(&altered,1,0x10000000)!=imageSetIdentity(&layout[1],1,0x10000000));
 altered=layout[1];altered.path=pathA;altered.pathSize=sizeof(pathA);assert(imageSetIdentity(&altered,1,0x10000000)!=imageSetIdentity(&layout[1],1,0x10000000));
 assert(imageSetIdentity(layout,2,0x20000000)!=identity);

 resolutionCache.init();assert(resolutionCache.ready);
 CachedResolution value=makeValue(key);
 assert(resolutionCache.publish(value)==PublishResult::Published);
 CachedResolution snapshot{};
 assert(resolutionCache.lookup(key,snapshot)&&snapshot.key==key&&snapshot.functionCount==1
        &&snapshot.functions[0].imageRelativeRVA==0x11000&&snapshot.proof==ProofClass::ReusableImmutableIdentity);
 assert(resolutionCache.publish(value)==PublishResult::Duplicate);
 // 结构非法、跨字段越界与重复函数索引都不得进入缓存。
 auto invalid=value;invalid.imageCount=0;assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 invalid=value;invalid.imageCount=ResolutionMaxImages+1;assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 invalid=value;invalid.functionCount=0;assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 invalid=value;invalid.functionCount=ResolutionMaxFunctions+1;assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 invalid=value;invalid.images[0].imageIndex=1;assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 invalid=value;invalid.images[0].textLength=0;assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 invalid=value;invalid.cacheSize=1;assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 invalid=value;invalid.functions[0].functionIndex=1;assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 invalid=value;invalid.functions[0].variant=2;assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 invalid=value;invalid.imageCount=2;invalid.functionCount=2;invalid.images[1]=invalid.images[0];invalid.images[1].imageIndex=1;
 invalid.functions[1]=invalid.functions[0];assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 invalid=value;invalid.proof=ProofClass::ReusableImmutableIdentity;invalid.images[0].mappingObjectId=0;
 assert(resolutionCache.publish(invalid)==PublishResult::Invalid);
 // 同键不同位置证据必须禁用，且后续发布仍不能恢复。
 const auto conflictKey=makeKey(uint32_t(FunctionGroup::VideoGuard),0,9);
 assert(resolutionCache.publish(makeValue(conflictKey,0x11000))==PublishResult::Published);
 assert(resolutionCache.publish(makeValue(conflictKey,0x12000))==PublishResult::Conflict);
 assert(!resolutionCache.lookup(conflictKey,snapshot));
 assert(resolutionCache.publish(makeValue(conflictKey,0x11000))==PublishResult::Conflict);
 // HintOnly 不参与热路径，但同布局的 Reusable 证明可以替换它。
 const auto upgradeKey=makeKey(uint32_t(FunctionGroup::DisplayCore),1,11);
 assert(resolutionCache.publish(makeValue(upgradeKey,0x11000,ProofClass::HintOnly))==PublishResult::Published);
 assert(resolutionCache.lookup(upgradeKey,snapshot)&&snapshot.proof==ProofClass::HintOnly);
 assert(resolutionCache.publish(makeValue(upgradeKey,0x11000,ProofClass::ReusableImmutableIdentity))==PublishResult::Published);
 assert(resolutionCache.lookup(upgradeKey,snapshot)&&snapshot.proof==ProofClass::ReusableImmutableIdentity);
 // 两个不同来源都宣称可复用时不得静默选择。
 const auto evidenceKey=makeKey(uint32_t(FunctionGroup::DisplaySubmit),2,12);
 auto first=makeValue(evidenceKey);first.images[0].mappingObjectId=1;
 auto second=makeValue(evidenceKey);second.images[0].mappingObjectId=2;
 assert(resolutionCache.publish(first)==PublishResult::Published);
 assert(resolutionCache.publish(second)==PublishResult::Conflict);
 assert(!resolutionCache.lookup(evidenceKey,snapshot));
 const auto keepKey=makeKey(uint32_t(FunctionGroup::DisplayScratch),3,13);
 assert(resolutionCache.publish(makeValue(keepKey))==PublishResult::Published);
 assert(resolutionCache.publish(makeValue(keepKey,0x11000,ProofClass::HintOnly))==PublishResult::Duplicate);
 assert(resolutionCache.lookup(keepKey,snapshot)&&snapshot.proof==ProofClass::ReusableImmutableIdentity);
 printf("PASS basic: full-field key compare, layout identity, publish/snapshot, invalid rejection, conflict disable, proof upgrade\n");
}
static void capacity(){
 resolutionCache.init();
 for(unsigned i=0;i<VerifiedResolutionCache::Capacity;i++){
  assert(resolutionCache.publish(makeValue(makeKey(uint32_t(FunctionGroup::VideoDecode),i,i+1)))==PublishResult::Published);
 }
 const auto extra=makeKey(uint32_t(FunctionGroup::VideoDecode),0xFFFF,0x123456);
 assert(resolutionCache.publish(makeValue(extra))==PublishResult::Full);
 CachedResolution snapshot;
 for(unsigned i=0;i<VerifiedResolutionCache::Capacity;i++){
  assert(resolutionCache.lookup(makeKey(uint32_t(FunctionGroup::VideoDecode),i,i+1),snapshot));
 }
 assert(!resolutionCache.lookup(extra,snapshot));
 // 满表时已有键的位置冲突仍按冲突禁用，不新增或覆盖条目。
 const auto existing=makeKey(uint32_t(FunctionGroup::VideoDecode),3,4);
 assert(resolutionCache.publish(makeValue(existing,0x13000))==PublishResult::Conflict);
 assert(!resolutionCache.lookup(existing,snapshot));
 assert(resolutionCache.publish(makeValue(extra))==PublishResult::Full);
 printf("PASS capacity: bounded registry, no eviction, full-table conflict handling\n");
}
static void concurrency(){
 resolutionCache.init();
 const auto key=makeKey(uint32_t(FunctionGroup::VideoDecode),0x55,0x77);
 const auto value=makeValue(key);
 std::atomic<unsigned> published{0},duplicate{0},conflict{0},other{0};
 {
  std::vector<std::thread> workers;
  for(unsigned t=0;t<4;t++)workers.emplace_back([&]{
   for(unsigned i=0;i<500;i++){
    switch(resolutionCache.publish(value)){
     case PublishResult::Published:published++;break;
     case PublishResult::Duplicate:duplicate++;break;
     case PublishResult::Conflict:conflict++;break;
     default:other++;break;
    }
   }
  });
  for(auto& worker:workers)worker.join();
 }
 assert(published==1&&duplicate==1999&&conflict==0&&other==0);
 std::atomic<unsigned> misses{0},torn{0};
 {
  std::vector<std::thread> readers;
  for(unsigned t=0;t<4;t++)readers.emplace_back([&]{
   for(unsigned i=0;i<4000;i++){
    CachedResolution snapshot{};
    if(!resolutionCache.lookup(key,snapshot)){misses++;continue;}
    if(snapshot.key!=key||snapshot.imageCount!=1||snapshot.functionCount!=1
       ||snapshot.functions[0].imageRelativeRVA!=0x11000||snapshot.images[0].relativeStart!=0x100000)torn++;
   }
  });
  std::vector<std::thread> writers;
  for(unsigned t=0;t<4;t++)writers.emplace_back([&,t]{
   for(unsigned i=0;i<200;i++)resolutionCache.publish(makeValue(makeKey(uint32_t(FunctionGroup::VideoGuard),t,i+1)));
  });
  for(auto& worker:readers)worker.join();
  for(auto& worker:writers)worker.join();
 }
 assert(misses==0&&torn==0);
 // 两个位置的并发发布只能留下一个一致结果或禁用条目，不能出现混合快照。
 const auto raceKey=makeKey(uint32_t(FunctionGroup::DisplayCore),0x66,0x88);
 std::atomic<bool> start{false};
 std::vector<std::thread> racers;
 for(unsigned t=0;t<4;t++)racers.emplace_back([&,t]{
  while(!start.load()){}
  resolutionCache.publish(makeValue(raceKey,t%2?0x11000:0x12000));
 });
 start=true;
 for(auto& worker:racers)worker.join();
 CachedResolution snapshot{};
 if(resolutionCache.lookup(raceKey,snapshot)){
  assert(snapshot.key==raceKey);
  assert(snapshot.functions[0].imageRelativeRVA==0x11000||snapshot.functions[0].imageRelativeRVA==0x12000);
 }
 printf("PASS concurrency: snapshot consistency, single publish under contention, disabled on conflicting positions\n");
}
int main(int argc,char** argv){
 assert(argc==2);
 const char* scenario=argv[1];
 if(strcmp(scenario,"basic")==0)basic();
 else if(strcmp(scenario,"capacity")==0)capacity();
 else if(strcmp(scenario,"concurrency")==0)concurrency();
 else return 2;
 return 0;
}
'''
    path, executable = WORK / 'test.cpp', WORK / 'test'
    path.write_text(source)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-Wall', '-Wextra', '-Werror', '-pthread',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    str(path), '-o', str(executable)], check=True, timeout=60)
    for scenario in ('basic', 'capacity', 'concurrency'):
        subprocess.run([str(executable), scenario], check=True, timeout=60)


if __name__ == '__main__':
    main()
