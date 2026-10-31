"""编译真实视频桥接，验证未知 UUID、重排、绑定与跨页事务。"""
from pathlib import Path
from user_patch_memory_harness import with_memory_helpers
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ('build/dynamic-cache/sequoia-video' if '--sequoia' in sys.argv else 'build/dynamic-cache/video')


def main():
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    (stub / 'IOUserClient.h').write_text((ROOT / 'tools/fixtures/fairplay-entitlement.hpp.in').read_text())
    boolean = WORK / 'stubs/libkern/c++'
    boolean.mkdir(parents=True, exist_ok=True)
    (boolean / 'OSBoolean.h').write_text('#pragma once\n#include <IOKit/IOUserClient.h>\n')
    util = WORK / 'stubs/Headers'
    util.mkdir(parents=True, exist_ok=True)
    (util / 'kern_util.hpp').write_text('#pragma once\n#include <IOKit/IOTypes.h>\n')
    source = (ROOT / 'NootedRed/UserVideoDecode.cpp').read_text()
    if '--baseline' in sys.argv:
        source = subprocess.check_output(['git','show','6c7b0804b5b115052fab5914eecfad7090b53eb1:NootedRed/UserVideoDecode.cpp'],cwd=ROOT,text=True)
    body = re.split(r'namespace\s*\{', source, maxsplit=1)[1].split('}    // namespace', 1)[0]
    body = with_memory_helpers(body)
    template = (ROOT / 'tools/fixtures/vcn-user-bridge.cpp.in').read_text()
    preamble = template[:template.index('static bool isWindowServer')]
    preamble = preamble.replace('#include "UserVideoDecodeData.hpp"', '#include <vector>\n#include "UserVideoDecodeData.hpp"\n#include "UserVideoDecodeResolver.hpp"')
    preamble += '#include "user-cache-dynamic.hpp"\n'
    preamble = preamble.replace('#define IONew(T,N) (new T[N])\n#define IODelete(P,T,N) delete[] P', r'''
#include <cstdlib>
#include <type_traits>
struct TransactionState;
static bool failTransaction=false,failDynamic=false;
static unsigned liveTransactions=0,liveDynamicPlans=0;
template<class T>T* trackedNew(size_t count){
 if constexpr(std::is_same_v<T,TransactionState>){if(failTransaction)return nullptr;}
 if constexpr(std::is_same_v<T,UserVideoDecodeResolver::Plan>){if(failDynamic)return nullptr;}
 auto* p=static_cast<T*>(malloc(sizeof(T)*count));if(!p)return nullptr;
 memset(p,0xa5,sizeof(T)*count);
 if constexpr(std::is_same_v<T,TransactionState>)liveTransactions++;
 if constexpr(std::is_same_v<T,UserVideoDecodeResolver::Plan>)liveDynamicPlans++;
 return p;
}
template<class T>void trackedDelete(T* p){
 if constexpr(std::is_same_v<T,TransactionState>){assert(liveTransactions>0);liveTransactions--;}
 if constexpr(std::is_same_v<T,UserVideoDecodeResolver::Plan>){assert(liveDynamicPlans>0);liveDynamicPlans--;}
 free(p);
}
#define IONew(T,N) trackedNew<T>(N)
#define IODelete(P,T,N) trackedDelete(P)
''')

    harness = preamble + r'''
// 当前源码使用 IOLock 与 map 门；测试夹具提供单进程替身，不涉及真实锁语义。
#include <mutex>
#include <condition_variable>
struct IOLock { std::mutex mutex; };
static IOLock* IOLockAlloc(){return new IOLock;}
static void IOLockFree(IOLock* p){delete p;}
static void IOLockLock(IOLock* p){p->mutex.lock();}
static void IOLockUnlock(IOLock* p){p->mutex.unlock();}
constexpr int THREAD_UNINT=0;
static std::condition_variable_any gateChanged;
static int IOLockSleep(IOLock* p,void*,int){std::unique_lock<std::mutex> held(p->mutex,std::adopt_lock);gateChanged.wait(held);held.release();return 0;}
static void IOLockWakeup(IOLock*,void*,bool){gateChanged.notify_all();}
#include <UserPatchMapGate.hpp>
static bool drmClient=false;
static const char* clientName="Music";
static std::set<uint64_t> drmTouchedPages;
static std::map<uint64_t,int> maxima;
static kern_return_t vm_protect(vm_map_t,uint64_t a,unsigned,int setMax,int flags){
 protects++;if(protects==failedProtect || failedProtectCalls.contains(protects))return 42;assert(protections.contains(a));
 drmTouchedPages.insert(a);
 int value=flags&7;
 if(setMax){maxima[a]=value;protections[a]&=value;}else{protections[a]=value;maxima[a]|=value;}
 return 0;}
BODY
static int original(proc_t,SharedRegionCheckArgs*,int* value){assert(liveTransactions==0&&liveDynamicPlans==0);*value=123;return 0;}
static int proc_pid(proc_t){return 217;}static void proc_name(int,char* name,int size){snprintf(name,size,"%s",drmClient?clientName:"VTDecoderXPC");}
static vm_map_t map(task_t){return reinterpret_cast<void*>(2);}
static std::map<uint64_t,std::vector<uint8_t>> cleanBlocks;
static bool cleanPage(uint64_t a){
 const uint64_t page=a&~uint64_t(4095);
 auto bi=blocks.upper_bound(page);if(bi==blocks.begin())return false;--bi;
 auto ci=cleanBlocks.upper_bound(page);if(ci==cleanBlocks.begin())return false;--ci;
 if(bi->first!=ci->first)return false;
 const uint64_t off=page-bi->first;
 return off+4096<=bi->second.size()&&off+4096<=ci->second.size()
        &&memcmp(bi->second.data()+off,ci->second.data()+off,4096)==0;
}
static kern_return_t query(vm_map_t,uint64_t* a,uint64_t* size,unsigned*,int* p,unsigned* count){
 assert(*count==VM_REGION_SUBMAP_SHORT_INFO_COUNT_64);auto*i=reinterpret_cast<vm_region_submap_short_info_data_64_t*>(p);
 i->protection=protections.contains(*a)?protections.at(*a):1;i->max_protection=maxima.contains(*a)?maxima.at(*a):7;
 i->is_submap=0;*size=4096;
 i->object_id=0x1234;i->share_mode=cleanPage(*a)?5:2;i->shadow_depth=0;return 0;}
static void setup(unsigned id){
 using namespace UserVideoDecodeData;cache(ResolverImages,ResolverFunctions,true);cleanBlocks=blocks;
 devicePatches=findDevice(id);assert(devicePatches);
 VPF::ResolutionDiagnostics::setEnabled(true);VPF::resolutionCache.init();
 drmClient=false;clientEntitlement=nullptr;drmTouchedPages.clear();maxima.clear();
 published={};seenStages=appliedCount=errorCount=0;originalCheck=reinterpret_cast<uint64_t>(original);readUser=reader;writeUser=writer;getTaskMap=map;queryRegion=query;
}
static void call(){SharedRegionCheckArgs args{0x1000};int value=0;assert(wrappedSharedRegionCheck(drmClient?reinterpret_cast<void*>(217):nullptr,&args,&value)==0&&value==123);assert(liveTransactions==0&&liveDynamicPlans==0);}
int main(){
 guardLock=IOLockAlloc();
 unsigned cases=0;
 // IONew 按原始内存模拟；逐一拒绝两处工作区分配，退出时必须全部释放。
 for(bool dynamicFailure:{false,true}){
  setup(0x1638);auto initial=blocks;
  failTransaction=!dynamicFailure;failDynamic=dynamicFailure;call();
  assert(blocks==initial&&writes==0&&protects==0);
  assert(properties.at("NRedVCNUserLastError")==KERN_RESOURCE_SHORTAGE);
  failTransaction=failDynamic=false;cases++;
 }
 for(unsigned id:{0x15e7U,0x1636U,0x1638U,0x164cU}){
  setup(id);UserVideoDecodeResolver::Plan plan;
  assert(UserVideoDecodeResolver::resolve(reader,base,devicePatches,plan));
  pages(plan.patches,PatchCount);auto initial=blocks;assert(protections.size()>UserVideoDecodeData::PageCount);
  call();if(!properties.contains("NRedVCNUserDynamicApplied")){for(auto[k,v]:properties)fprintf(stderr,"%s=%u\n",k.c_str(),v);}assert(properties.contains("NRedVCNUserDynamicApplied"));rx();assert(writes==PatchCount);
  for(unsigned i=0;i<PatchCount;i++){uint8_t b[UserVideoDecodeData::MaxPatchSize];assert(reader(base+plan.patches[i].offset,b,plan.patches[i].size)==0);assert(memcmp(b,plan.patches[i].patched,plan.patches[i].size)==0);}
  auto before=writes;call();assert(writes==before&&properties.at("NRedVCNUserD07")==1);cases++;
  for(unsigned entry=1;entry<=PatchCount;entry++){
   setup(id);pages(plan.patches,PatchCount);initial=blocks;failWrite=entry;call();assert(blocks==initial);rx();assert(!properties.contains("NRedVCNUserDynamicApplied"));cases++;
  }
  for(unsigned page=1;page<=protections.size();page++){
   setup(id);pages(plan.patches,PatchCount);initial=blocks;failedProtect=page;call();assert(blocks==initial&&writes==0);rx();cases++;
  }
  setup(id);pages(plan.patches,PatchCount);initial=blocks;failedProtectCalls={unsigned(protections.size())+1,unsigned(protections.size())+2};call();assert(blocks==initial);rx();cases++;
 }
 for(unsigned kind=0;kind<12;kind++){
  setup(0x15e7);using namespace UserVideoDecodeData;
  switch(kind){
   case 0:word32(base+452,8193);break;
   case 1:word32(base+448,0xfffffff0);break;
   case 2:word32(imageAddresses[0],0);break;
   case 3:word32(imageAddresses[0]+36,7);break;
   case 4:word32(functionAddresses[0]+100,0);break;
   case 5:word64(base+0x1000+32,imageAddresses[0]-0x200000);word32(base+0x1018+32,0x3000);break;
   case 6:word64(base+232,0xffffffffffffffffULL);break;
   case 7:failedRead=functionAddresses[0];break;
   case 8:{std::vector<uint8_t> b(ResolverFunctions[0].size);assert(reader(functionAddresses[0],b.data(),b.size())==0);put(imageAddresses[0]+0x5e000,b.data(),b.size());}break;
   case 9:word64(base+0x7000000,0);break;
   case 10:rel(functionAddresses[0],ResolverFunctions[0].originalRefs[0].offset,base+0x7010000);break;
   case 11:put(base,"invalid",7);break;
  }
  auto initial=blocks;call();assert(blocks==initial&&writes==0&&protects==0);cases++;
 }
#ifndef BASELINE
 // 各 FairPlay 客户端共用解码子集，在重排及跨页缓存中仍对全部已选页实施事务回滚。
 for(unsigned id:{0x15e7U,0x1636U,0x1638U,0x164cU})for(const char* name:{"Music","TV","Other"}){
  clientName=name;
  setup(id);UserVideoDecodeResolver::Plan plan;
  assert(UserVideoDecodeResolver::resolve(reader,base,devicePatches,plan));
  auto enable=[&]{drmClient=true;clientEntitlement=kOSBooleanTrue;pages(plan.patches,PatchCount);};
  std::set<uint64_t> selectedPages;
  constexpr unsigned selectedMask=0x7F87FE;
  for(unsigned i=0;i<PatchCount;i++)if(selectedMask&(1U<<i)){
   const auto& p=plan.patches[i];
   for(auto a=(base+p.offset)&~uint64_t(4095);a<=((base+p.offset+p.size-1)&~uint64_t(4095));a+=4096)selectedPages.insert(a);
  }
  enable();call();rx();
  if(writes!=18||drmTouchedPages!=selectedPages){
   fprintf(stderr,"driver-only id=%x writes=%u touched=%zu expected=%zu\n",id,writes,drmTouchedPages.size(),selectedPages.size());
   for(auto[k,v]:properties)fprintf(stderr,"%s=%u\n",k.c_str(),v);
  }
  assert(writes==18&&drmTouchedPages==selectedPages);
  assert(properties.at("NRedDRMVideoState")==2&&properties.at("NRedDRMVideoPID")==217);
  {SharedRegionCheckArgs empty{0};int value=0;assert(wrappedSharedRegionCheck(reinterpret_cast<void*>(217),&empty,&value)==0&&value==123);}
  assert(properties.at("NRedDRMVideoState")==2);
  assert(properties.at("NRedDRMVideoLastPID")==217&&properties.at("NRedDRMVideoLastStage")==2);
  for(unsigned i=0;i<PatchCount;i++){
   uint8_t b[UserVideoDecodeData::MaxPatchSize];auto& p=plan.patches[i];
   assert(reader(base+p.offset,b,p.size)==0);
   assert(memcmp(b,(selectedMask&(1U<<i))?p.patched:p.original,p.size)==0);
  }
  auto before=writes;call();assert(writes==before&&properties.at("NRedDRMVideoState")==3);cases++;
  for(unsigned entry=1;entry<=18;entry++){
   setup(id);enable();auto initial=blocks;failWrite=entry;call();rx();
   assert(blocks==initial&&properties.at("NRedDRMVideoState")==1);cases++;
  }
  for(unsigned page=1;page<=selectedPages.size();page++){
   setup(id);enable();auto initial=blocks;failedProtect=page;call();rx();
   assert(blocks==initial&&writes==0&&properties.at("NRedDRMVideoState")==1);cases++;
  }
  setup(id);enable();auto initial=blocks;failedProtectCalls={unsigned(selectedPages.size())+1,unsigned(selectedPages.size())+2};
  call();rx();assert(blocks==initial&&properties.at("NRedDRMVideoState")==1);cases++;
  setup(id);enable();put(base+plan.patches[0].offset,plan.patches[0].patched,plan.patches[0].size);initial=blocks;
  call();assert(blocks==initial&&writes==0&&protects==0&&properties.at("NRedDRMVideoState")==1);cases++;
 }
#endif
 // 复用同一公共 Input 时重建映像索引，读取预算累计，不能残留上一轮地址。
 setup(0x15e7);UserSharedCache::Input reused{reader,{}};UserVideoDecodeResolver::Plan reusedPlan;
 for(unsigned i=0;i<2;i++)assert(UserCacheResolver::resolve(reused,base,UserVideoDecodeData::ResolverImages,
 UserVideoDecodeData::ResolverFunctions,devicePatches,UserVideoDecodeData::ResolverLocations,reusedPlan.patches,
 &reusedPlan.original[0][0],&reusedPlan.patched[0][0],UserVideoDecodeData::MaxPatchSize,1));
 // 指针绑定、rel32 溢出与预算耗尽必须拒绝，且解析器本身不写缓存。
 setup(0x15e7);UserSharedCache::Input input{reader,{}};input.reads=32768;UserVideoDecodeResolver::Plan plan;
 assert(!UserCacheResolver::resolve(input,base,UserVideoDecodeData::ResolverImages,UserVideoDecodeData::ResolverFunctions,devicePatches,
 UserVideoDecodeData::ResolverLocations,plan.patches,&plan.original[0][0],&plan.patched[0][0],UserVideoDecodeData::MaxPatchSize));
 printf("PASS %u video dynamic bridge cases: four devices; shifted images/functions/GOT; cross-page rollback; content/metadata/target rejection\n",cases);
}
'''
    if '--sequoia' in sys.argv:
        harness = harness.replace('cache(ResolverImages,ResolverFunctions,true)', 'cache(ResolverImages,ResolverFunctions,false)')
        harness = harness.replace('UserVideoDecodeData::MaxPatchSize,1)', 'UserVideoDecodeData::MaxPatchSize,0)')
        harness = harness.replace('UserVideoDecodeData::', 'UserSequoiaVideoData::')
        harness = harness.replace('using namespace UserVideoDecodeData;', 'using namespace UserSequoiaVideoData;')
        harness = harness.replace('devicePatches,plan)', 'devicePatches,plan,true)')
        harness = harness.replace('0x7F87FE', '0x7FE1FE')
        harness = harness.replace('drmClient=false;clientEntitlement', 'sequoia=true;drmClient=false;clientEntitlement')
    # 从实际源文件中提取的桥接引用进程诊断函数。
    uuid = 'UserSequoiaVideoData::CacheUUID' if '--sequoia' in sys.argv else 'UserVideoDecodeData::CacheUUID'
    harness = harness.replace('BODY', 'static bool referenceUUID=false;\nBODY')
    harness = harness.replace('cleanBlocks=blocks;', f'if(referenceUUID)put(base+88,{uuid},16);cleanBlocks=blocks;')
    harness = harness.replace('int main(){', 'int main(int argc,char**){referenceUUID=argc>1;')
    harness = harness.replace('BODY', body)
    harness = harness.replace('static kern_return_t vm_protect', 'static int proc_pid(proc_t);static void proc_name(int,char*,int);\nstatic kern_return_t vm_protect', 1)
    if '--baseline' in sys.argv:
        harness = harness.replace('call();if(!properties.contains("NRedVCNUserDynamicApplied"))', 'call();assert(writes==0&&protects==0);puts("PASS baseline reproduces unknown-cache rejection");return 0;if(!properties.contains("NRedVCNUserDynamicApplied"))',1)
    file, exe = WORK / 'test.cpp', WORK / 'test'
    file.write_text(harness)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if '--sanitize' in sys.argv else []
    if '--baseline' in sys.argv:
        flags.append('-DBASELINE')
    subprocess.run(['clang++', '-std=c++23', '-O2', *flags, '-I'+str(WORK/'stubs'),
                    '-I'+str(ROOT/'NootedRed'), '-I'+str(ROOT/'tools/fixtures'), str(file), '-o', str(exe)], check=True, timeout=60)
    if '--known-uuid' not in sys.argv:
        subprocess.run([str(exe)], check=True, timeout=60)
    subprocess.run([str(exe), '--known-uuid'], check=True, timeout=60)


if __name__ == '__main__':
    main()
