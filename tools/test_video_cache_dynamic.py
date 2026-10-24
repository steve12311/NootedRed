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
    harness = preamble + r'''
static bool drmClient=false;
static const char* clientName="Music";
static std::set<uint64_t> drmTouchedPages;
static kern_return_t vm_protect(vm_map_t,uint64_t a,unsigned,int setMax,int flags){
 protects++;if(protects==failedProtect || failedProtectCalls.contains(protects))return 42;assert(protections.contains(a));
 drmTouchedPages.insert(a);
 if(setMax)protections[a]&=flags&7;else protections[a]=flags&7;return 0;}
BODY
static int original(proc_t,SharedRegionCheckArgs*,int* value){*value=123;return 0;}
static int proc_pid(proc_t){return 217;}static void proc_name(int,char* name,int size){snprintf(name,size,"%s",drmClient?clientName:"VTDecoderXPC");}
static vm_map_t map(task_t){return reinterpret_cast<void*>(2);}
static kern_return_t query(vm_map_t,uint64_t* a,uint64_t* size,unsigned*,int* p,unsigned* count){
 assert(*count==VM_REGION_SUBMAP_SHORT_INFO_COUNT_64);auto*i=reinterpret_cast<vm_region_submap_short_info_data_64_t*>(p);
 i->protection=protections.at(*a);i->is_submap=0;*size=4096;return 0;}
static void setup(unsigned id){
 using namespace UserVideoDecodeData;cache(ResolverImages,ResolverFunctions);devicePatches=findDevice(id);assert(devicePatches);
 drmClient=false;clientEntitlement=nullptr;drmTouchedPages.clear();
#ifndef BASELINE
 fairPlayDriverOnly=false;
#endif
 seenStages=appliedCount=errorCount=0;originalCheck=reinterpret_cast<uint64_t>(original);readUser=reader;writeUser=writer;getTaskMap=map;queryRegion=query;
}
static void call(){SharedRegionCheckArgs args{0x1000};int value=0;assert(wrappedSharedRegionCheck(drmClient?reinterpret_cast<void*>(217):nullptr,&args,&value)==0&&value==123);}
int main(){
 unsigned cases=0;
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
  auto enable=[&]{fairPlayDriverOnly=drmClient=true;clientEntitlement=kOSBooleanTrue;pages(plan.patches,PatchCount);};
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
 &reusedPlan.original[0][0],&reusedPlan.patched[0][0],UserVideoDecodeData::MaxPatchSize));
 // 指针绑定、rel32 溢出与预算耗尽必须拒绝，且解析器本身不写缓存。
 setup(0x15e7);UserSharedCache::Input input{reader,{}};input.reads=32768;UserVideoDecodeResolver::Plan plan;
 assert(!UserCacheResolver::resolve(input,base,UserVideoDecodeData::ResolverImages,UserVideoDecodeData::ResolverFunctions,devicePatches,
 UserVideoDecodeData::ResolverLocations,plan.patches,&plan.original[0][0],&plan.patched[0][0],UserVideoDecodeData::MaxPatchSize));
 printf("PASS %u video dynamic bridge cases: four devices; shifted images/functions/GOT; cross-page rollback; content/metadata/target rejection\n",cases);
}
'''
    if '--sequoia' in sys.argv:
        harness = harness.replace('UserVideoDecodeData::', 'UserSequoiaVideoData::')
        harness = harness.replace('using namespace UserVideoDecodeData;', 'using namespace UserSequoiaVideoData;')
        harness = harness.replace('devicePatches,plan)', 'devicePatches,plan,true)')
        harness = harness.replace('0x7F87FE', '0x7FE1FE')
        harness = harness.replace('drmClient=false;clientEntitlement', 'sequoia=true;legacyProfile=nullptr;drmClient=false;clientEntitlement')
    # 从实际源文件中提取的桥接引用进程诊断函数。
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
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
