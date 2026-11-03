"""编译真实 OpenGL 钩子，检查无文件准备的初始化、当前页匹配、ABI 与页面写保护。"""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/opengl/bridge'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sanitize', action='store_true')
    parser.add_argument('--stack', action='store_true', help='检查优化构建中的函数栈帧预算')
    args = parser.parse_args()
    source = (ROOT / 'NootedRed/UserOpenGL.cpp').read_text()
    body = source.split('namespace\n{', 1)[1].split('}    // namespace', 1)[0]
    init = source[source.index('void UserOpenGL::registerKext('):]
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    harness = r'''
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <cstdlib>
#include <UserOpenGLData.hpp>
#include <UserOpenGLResolver.hpp>
#include <VPF/DisplayManifestData.hpp>
#include <VPF/ValidatedPageBackend.hpp>
#include <VPF/DisplayRuntime.hpp>
using vnode_t=void*;using memory_object_t=void*;using memory_object_offset_t=uint64_t;using vm_size_t=uint64_t;
using boolean_t=int;using mach_vm_address_t=uint64_t;using thread_call_param_t=void*;using thread_call_t=void*;
using vfs_context_t=void*;
using thread_call_func_t=void(*)(void*,void*);
constexpr int KERN_SUCCESS=0;
static bool flag=true,allocateOK=true,pending=false,locked=false,inHook=false;
static unsigned major=25,pci=0x1638,allocated=0,freed=0,writeAccess=0,queued=0,validCalls=0;
static int validValue=1,taintValue=0,nxValue=0,rangeResult=1;
static std::set<unsigned> failedWrites;
static std::function<void()> onEnable;
static std::map<std::string,unsigned> properties;
static thread_call_func_t callback=nullptr;
struct NRed{static NRed& singleton(){static NRed n;return n;}unsigned getDeviceID(){return pci;}
 void setProp32(const char* name,unsigned value){assert(!inHook&&!locked);properties[name]=value;}};
struct Version{unsigned major(){return ::major;}};static Version currentKernelVersion(){return {};}
static bool checkKernelArgument(const char* name){assert(strcmp(name,"-NRedDisplayFix")==0);return flag;}
namespace UserPatchCoordinator {
static UInt32 requestedDisplayGroups(){return VPF::requestedGroupsForDisplayFix(checkKernelArgument("-NRedDisplayFix"),major);}
}
static thread_call_t thread_call_allocate(thread_call_func_t cb,void*){allocated++;callback=cb;return allocateOK?reinterpret_cast<void*>(1):nullptr;}
constexpr unsigned THREAD_CALL_PRIORITY_KERNEL=1;
static thread_call_t thread_call_allocate_with_priority(thread_call_func_t cb,void* p,unsigned priority){
 assert(priority==THREAD_CALL_PRIORITY_KERNEL);return thread_call_allocate(cb,p);
}
constexpr unsigned THREAD_CALL_OPTIONS_ONCE=1;
static thread_call_t thread_call_allocate_with_options(thread_call_func_t cb,void* p,unsigned priority,unsigned options){
 assert(options==THREAD_CALL_OPTIONS_ONCE);return thread_call_allocate_with_priority(cb,p,priority);
}
static void thread_call_free(thread_call_t p){assert(p);freed++;}
static void thread_call_enter(thread_call_t p){assert(p&&!locked);pending=true;queued++;}
static void flush(){assert(!inHook&&!locked);if(pending){pending=false;
 callback(nullptr,nullptr);}}
namespace MachInfo{static int setKernelWriting(bool enable,void*){
 writeAccess++;
 if(enable){assert(!locked);locked=true;if(onEnable){auto call=onEnable;onEnable=nullptr;call();}}
 else assert(locked);
 if(failedWrites.contains(writeAccess)){locked=false;return 5;}
 if(!enable)locked=false;return 0;
}}
static const char* vnodeName="AMDRadeonX5000GLDriver";static unsigned names=0,putnames=0;
static const char* vnode_getname(vnode_t p){assert(p);names++;return vnodeName;}
static void vnode_putname(const char* p){assert(p);putnames++;}
static off_t verifyOffset=0;static UInt32 verifyFlags=0;
static const void* verifyExpected=nullptr;static bool allocateBufferOK=true;static unsigned buffers=0,verifyCalls=0;
[[maybe_unused]] static void* kern_os_malloc(size_t size){assert(!inHook&&!locked);if(!allocateBufferOK)return nullptr;buffers++;return malloc(size);}
[[maybe_unused]] static void kern_os_free(void* p){assert(p&&buffers);buffers--;free(p);}
static int stockVerify(vnode_t vp,off_t off,const void* data,size_t size,size_t* block,void** context,UInt32 flags,vfs_context_t ctx,void* kind){
 assert(vp==reinterpret_cast<void*>(1)&&off==verifyOffset&&flags==verifyFlags);
 assert(block&&context&&ctx==reinterpret_cast<void*>(0x3333)&&kind==(major==25?reinterpret_cast<void*>(0x4444):nullptr));
 *block=131072;*context=reinterpret_cast<void*>(0x5555);verifyCalls++;return !data||!verifyExpected||memcmp(data,verifyExpected,size)==0?0:94;
}
// 内联 VNOP_VERIFY 直接分派到文件系统操作，不经过导出的包装函数。
struct FilesystemArgs {
 void* descriptor;vnode_t vnode;off_t offset;const void* data;size_t size;size_t* block;void** context;
 UInt32 flags;vfs_context_t ctx;void* kind;
};
using FilesystemVerifier=int(*)(void*);
static int stockFilesystem(void* raw){
 FilesystemArgs a{};memcpy(&a,raw,major==25?80:72);assert(a.descriptor==reinterpret_cast<void*>(0x2222));
 return stockVerify(a.vnode,a.offset,a.data,a.size,a.block,a.context,a.flags,a.ctx,a.kind);
}
static FilesystemVerifier filesystemVerify=stockFilesystem;
static int verifyInlined(void* data,size_t size,off_t offset,UInt32 flags=0){
 size_t block=0;void* context=nullptr;verifyOffset=offset;verifyFlags=flags;
 FilesystemArgs a{reinterpret_cast<void*>(0x2222),reinterpret_cast<void*>(1),offset,data,size,&block,&context,flags,
                  reinterpret_cast<void*>(0x3333),major==25?reinterpret_cast<void*>(0x4444):nullptr};
 // 精确分配旧版 72 字节参数，ASan 会捕获对 Darwin 25 尾字段的越界读取。
 std::vector<uint8_t> raw(major==25?80:72);memcpy(raw.data(),&a,raw.size());auto before=raw;
 auto result=filesystemVerify(raw.data());
 assert(raw==before&&buffers==0&&block==131072&&context==reinterpret_cast<void*>(0x5555));return result;
}
static int verify(void* data,size_t size=4096,UInt32 flags=0,off_t offset=0xe000){
 return verifyInlined(data,size,offset,flags);
}
static vnode_t expectedVnode=reinterpret_cast<void*>(0x1234567812340000ULL);
static memory_object_t expectedPager=reinterpret_cast<void*>(0xabcdef0098760000ULL);
static uint64_t expectedOffset=0x56789000ULL,expectedSize=0;static const void* expectedData=nullptr;
static void stockPage(vnode_t vp,memory_object_t pager,uint64_t offset,const void* data,int* valid,int* taint,int* nx){
 assert(vp==expectedVnode&&pager==expectedPager&&offset==expectedOffset&&data==expectedData);validCalls++;
 if(valid)*valid=validValue;if(taint)*taint=taintValue;if(nx)*nx=nxValue;
}
static boolean_t stockRange(vnode_t vp,memory_object_t pager,uint64_t offset,const void* data,uint64_t size,unsigned* taint){
 assert(vp==expectedVnode&&pager==expectedPager&&offset==expectedOffset&&data==expectedData&&size==expectedSize);validCalls++;
 if(taint)*taint=unsigned(taintValue);return rangeResult;
}
struct KernelPatcher {
 static constexpr int KernelID=0;static inline void* kernelWriteLock=nullptr;
 struct KextInfo {static constexpr size_t Unloaded=0;const char* id;const char** paths;size_t pathNum;
  bool sys[6];bool user[2];size_t loadIndex;};
 unsigned routes=0;bool pageOK=true,rangeOK=true,verifyOK=true;
 struct RouteRequest {const char* name;template<class F>RouteRequest(const char* s,F f,uint64_t& original):name(s){
  assert(strcmp(s,"_VNOP_VERIFY")!=0);
  if(strcmp(s,"_apfs_vnop_verify")==0){
   original=reinterpret_cast<uint64_t>(stockFilesystem);filesystemVerify=reinterpret_cast<FilesystemVerifier>((uintptr_t)f);return;
  }
  original=reinterpret_cast<uint64_t>(strcmp(s,"_cs_validate_page")==0?reinterpret_cast<void*>(stockPage):reinterpret_cast<void*>(stockRange));
 }};
 bool routeMultipleLong(size_t id,RouteRequest* r,unsigned n,uint64_t slide=0,size_t size=0){
  assert(n==1);routes++;
  if(strcmp(r->name,"_apfs_vnop_verify")==0){assert(id==77&&slide==0x100000&&size==0x200000);return verifyOK;}
  assert(id==KernelID);return strcmp(r->name,"_cs_validate_page")==0?pageOK:rangeOK;
 }
 void clearError(){}
};
static unsigned registrations=0;
struct Lilu {void onKextLoadForce(KernelPatcher::KextInfo* k){
 assert(strcmp(k->id,"com.apple.filesystems.apfs")==0&&k->pathNum==1&&k->sys[0]);k->loadIndex=77;registrations++;
}};static Lilu lilu;
namespace UserOpenGL{void registerKext();void init(KernelPatcher&);void processKext(KernelPatcher&,size_t,mach_vm_address_t,size_t);}
namespace Actual {
BODY
}
INIT

static void reset(unsigned os=25,unsigned id=0x1638){
 if(Actual::reportCall)thread_call_free(Actual::reportCall);
 Actual::reportCall=nullptr;Actual::device=nullptr;Actual::manifests[0]=Actual::manifests[1]=nullptr;
 Actual::appliedMask=Actual::dynamicMask=Actual::errorMask=Actual::planMask=Actual::evidenceMask=0;
 Actual::groupStates[0]=Actual::groupStates[1]=0;Actual::originalVerify=0;Actual::verifyMask=0;registrations=0;
 properties.clear();failedWrites.clear();onEnable=nullptr;pending=locked=inHook=false;
 flag=allocateOK=true;major=os;pci=id;allocated=freed=writeAccess=queued=validCalls=0;
 validValue=rangeResult=1;taintValue=nxValue=0;vnodeName="AMDRadeonX5000GLDriver";names=putnames=0;
 expectedVnode=reinterpret_cast<void*>(0x1234567812340000ULL);expectedOffset=0xe000;
 filesystemVerify=stockFilesystem;verifyExpected=nullptr;allocateBufferOK=true;buffers=verifyCalls=0;
}
static void start(KernelPatcher& patcher){
 UserOpenGL::registerKext();UserOpenGL::init(patcher);assert(patcher.routes==0);
 UserOpenGL::processKext(patcher,78,0x100000,0x200000);assert(patcher.routes==0);
 UserOpenGL::processKext(patcher,77,0x100000,0x200000);
}
static void init(){KernelPatcher patcher;start(patcher);assert(registrations==1);
 assert(patcher.routes==3&&properties.at("NRedOpenGLBackends")==3);
 assert(!pending&&queued==0&&properties.at("NRedOpenGLRevision")==7&&properties.at("NRedOpenGLPageLocal")==1);
 assert(properties.at("NRedOpenGLVPFRevision")==1&&properties.at("NRedOpenGLVPFManifestMask")==3);
 assert(!properties.contains("NRedOpenGLDynamicPending"));
 auto routes=patcher.routes;UserOpenGL::processKext(patcher,77,0x100000,0x200000);assert(patcher.routes==routes);}
static void page(void* data,bool validPointer=true){
 int valid=99,taint=99,nx=99;expectedData=data;inHook=true;
 Actual::validatePage(expectedVnode,expectedPager,expectedOffset,data,validPointer?&valid:nullptr,&taint,&nx);
 inHook=false;assert((!validPointer||valid==validValue)&&taint==taintValue&&nx==nxValue&&!locked);
}
static void range(void* data,uint64_t size){
 unsigned taint=99;expectedData=data;expectedSize=size;inHook=true;
 auto result=Actual::validateRange(expectedVnode,expectedPager,expectedOffset,data,size,&taint);
 inHook=false;assert(result==rangeResult&&taint==unsigned(taintValue)&&!locked);
}
int main(){
 unsigned cases=0;
 for(auto& d:UserOpenGLData::Devices)for(unsigned index=0;index<2;index++){
  auto& p=d.pages[index];std::array<uint8_t,4096> bytes,expected;
  const auto setup=[&]{reset(d.major,d.deviceID);init();memcpy(bytes.data(),p.original,4096);expected=bytes;
   expectedOffset=d.patches[index].offset-p.offset;memcpy(expected.data()+p.offset,p.patched,p.size);};
  setup();page(bytes.data());assert(bytes==expected&&writeAccess==2&&names==putnames&&Actual::appliedMask==(1U<<index));
  assert(!properties.contains("NRedOpenGLSEApplied"));flush();
  assert(properties.at(index?"NRedOpenGLDualQuadApplied":"NRedOpenGLSEApplied")==1);
  assert(properties.at("NRedOpenGLVPFPlanVerified")==0);assert(Actual::evidenceMask==(1U<<index));
  assert(properties.at(index?"NRedOpenGLDualQuadGroupState":"NRedOpenGLHwInfoGroupState")
         ==unsigned(VPF::DisplayGroupState::Installed));
  // 解压页入会复用已缓存的补丁页；文件系统仍必须校验原始字节，且不能改写执行页。
  verifyExpected=p.original;auto execution=bytes;
  assert(verify(bytes.data(),4096,0,expectedOffset)==0&&verifyCalls==1&&bytes==execution);cases++;
  assert(verifyInlined(bytes.data(),4096,expectedOffset)==0&&bytes==execution);cases++;
  flush();assert(properties.at("NRedOpenGLVerifyObserved")==1&&properties.at("NRedOpenGLVerifyRestored")==1
   &&properties.at("NRedOpenGLVerifyRejected")==0&&properties.at("NRedOpenGLVerifyAllocationFailed")==0);cases++;
  auto diagnostics=queued;
  assert(verifyInlined(bytes.data(),4096,expectedOffset)==0&&queued==diagnostics);cases++;
  assert(verifyInlined(nullptr,0,expectedOffset)==0&&queued==diagnostics);cases++;
  assert(verify(bytes.data(),4096,2,expectedOffset)==0&&bytes==execution);cases++;
  // 预计算哈希、上下文管理及未知 flags 不能被当作文件数据修改。
  for(unsigned flags:{1U,4U,8U,16U}){
   assert(verify(bytes.data(),4096,flags,expectedOffset)==94&&bytes==execution);cases++;
  }
  allocateBufferOK=false;assert(verify(bytes.data(),4096,0,expectedOffset)==94&&bytes==execution);cases++;
  flush();assert(properties.at("NRedOpenGLVerifyAllocationFailed")==1);cases++;
  allocateBufferOK=true;
  bytes[(p.offset+p.size)%4096]^=1;auto corrupted=bytes;
  assert(verify(bytes.data(),4096,0,expectedOffset)==94&&bytes==corrupted);cases++;
  flush();assert(properties.at("NRedOpenGLVerifyRejected")==1);cases++;
  bytes=execution;bytes[p.offset]^=1;corrupted=bytes;
  assert(verify(bytes.data(),4096,0,expectedOffset)==94&&bytes==corrupted);cases++;
  bytes=execution;vnodeName="other";
  assert(verify(bytes.data(),4096,0,expectedOffset)==94&&bytes==execution);cases++;
  vnodeName="AMDRadeonX5000GLDriver";
  flush();auto before=writeAccess,queueBefore=queued;page(bytes.data());assert(writeAccess==before&&queued==queueBefore);cases++;
  // XNU 整页验证返回四位全真位图，不能仅接受旧布尔值 1。
  setup();validValue=0xf;page(bytes.data());flush();assert(bytes==expected&&Actual::appliedMask==(1U<<index));cases++;
  for(int valid:{-1,2,3,7,14,16}){
   setup();validValue=valid;auto old=bytes;page(bytes.data());assert(bytes==old&&writeAccess==0);cases++;
  }
  // 参考页外部变化仍可动态定位，修复块内部变化则拒绝。
  for(unsigned off:{p.offset,p.offset+p.size-1}){
   setup();bytes[off]^=1;auto old=bytes;page(bytes.data());assert(bytes==old&&writeAccess==0&&Actual::appliedMask==0);cases++;
  }
  for(unsigned off:{0U,p.offset-1,p.offset+p.size}){
   setup();bytes[off]^=1;expected[off]^=1;page(bytes.data());flush();
   assert(bytes==expected&&Actual::dynamicMask==(1U<<index));cases++;
  }
  for(const char* name:{"other","AMDRadeonX5000GLDriver.fake","xAMDRadeonX5000GLDriver",static_cast<const char*>(nullptr)}){
   setup();vnodeName=name;page(bytes.data());assert(bytes!=expected&&writeAccess==0);assert(putnames==(name?names:0));cases++;
  }
  for(unsigned invalid=0;invalid<8;invalid++){
   setup();switch(invalid){case 0:validValue=0;break;case 1:validValue=2;break;case 2:taintValue=1;break;
    case 3:nxValue=1;break;case 4:expectedOffset++;break;case 5:expectedVnode=nullptr;break;
    case 7:expectedOffset=0xfffffffffffff000ULL;break;default:break;}
   auto old=bytes;page(bytes.data(),invalid!=6);assert(bytes==old&&writeAccess==0);cases++;
  }
  setup();flag=false;auto denied=bytes;page(bytes.data());
  assert(bytes==denied&&writeAccess==0&&Actual::appliedMask==0);cases++;
  setup();failedWrites={1};page(bytes.data());flush();assert(bytes!=expected&&properties.at("NRedOpenGLWriteRejected")==1&&Actual::appliedMask==0);cases++;
  setup();failedWrites={2};page(bytes.data());flush();assert(bytes==expected&&writeAccess==4&&Actual::appliedMask==(1U<<index));cases++;
  setup();failedWrites={2,4};page(bytes.data());flush();assert(bytes==expected&&Actual::appliedMask==0&&properties.at("NRedOpenGLRestoreFailed")==1);cases++;
  setup();onEnable=[&]{bytes=expected;};page(bytes.data());assert(bytes==expected&&Actual::appliedMask==0&&writeAccess==2);cases++;
  // 同一页内容在获取写锁前改变，不能用旧计划覆盖新的内容。
  setup();onEnable=[&]{bytes[p.offset]^=1;};auto old=bytes;old[p.offset]^=1;
  page(bytes.data());assert(bytes==old&&Actual::appliedMask==0);cases++;
  setup();std::vector<uint8_t> data(4096*2+17,0x5a),wanted;
  memcpy(data.data(),p.original,4096);memcpy(data.data()+4096,p.original,4096);wanted=data;
  memcpy(wanted.data()+p.offset,p.patched,p.size);
  range(data.data(),data.size());assert(data==wanted&&Actual::appliedMask==(1U<<index));flush();cases++;
  // 第二页的同样字节位于错误文件偏移，相对目标校验应拒绝。
  for(unsigned invalid=0;invalid<6;invalid++){
   setup();auto old=bytes;switch(invalid){case 0:rangeResult=0;break;case 1:taintValue=2;break;case 2:expectedOffset++;break;
    case 5:expectedOffset=0xfffffffffffff000ULL;break;default:break;}
   range(invalid==3?nullptr:bytes.data(),invalid==4?4095:4096);assert(bytes==old&&writeAccess==0);cases++;
  }
 }
 // 两个动态块放在同一页，检查页内定位、相对重建及两项诊断。
 for(auto& d:UserOpenGLData::Devices){
  reset(d.major,d.deviceID);init();expectedOffset=0x40000;
  std::array<uint8_t,4096> bytes;bytes.fill(0xcc);
  const unsigned positions[]={300,2000};
  for(unsigned k=0;k<2;k++){
   const auto& p=d.patches[k];const auto& f=d.resolver->functions[k];auto start=d.resolver->locations[k].offset;
   memcpy(bytes.data()+positions[k],p.original,p.size);
   for(unsigned r=0;r<f.originalCount;r++)assert(UserSharedCache::setRelative(bytes.data()+positions[k],f.originalRefs[r].offset-start,
        expectedOffset+positions[k],f.originalRefs[r].rva));
  }
  auto original=bytes;page(bytes.data());flush();
  assert(Actual::appliedMask==3&&Actual::dynamicMask==3&&properties.at("NRedOpenGLDynamicApplied")==1&&writeAccess==4);
  assert(Actual::planMask==3&&Actual::evidenceMask==3&&properties.at("NRedOpenGLVPFPlanVerified")==1);
  assert(properties.at("NRedOpenGLVPFEvidenceMask")==3);
  verifyExpected=original.data();auto execution=bytes;
  assert(verify(bytes.data(),bytes.size(),0,expectedOffset)==0&&bytes==execution);cases++;
  // 一个较大校验块同时包含已缓存补丁页与新解压的原始页。
  std::vector<uint8_t> mixed(65536,0x5a),unmodified;
  memcpy(mixed.data(),bytes.data(),4096);unmodified=mixed;
  memcpy(unmodified.data(),original.data(),4096);verifyExpected=unmodified.data();
  assert(verify(mixed.data(),mixed.size(),0,expectedOffset)==0&&memcmp(mixed.data(),bytes.data(),4096)==0);cases++;
  for(unsigned i=0;i<4096;i++)if(!(i>=300&&i<556)&&!(i>=2000&&i<2142))assert(bytes[i]==original[i]);cases++;
  // 重复校验不写入、不重复排队；另一个 CPU 已写入的页面也能识别。
  auto before=queued;page(bytes.data());assert(writeAccess==4&&queued==before);cases++;
 }
 for(unsigned bits=0;bits<4;bits++){
  reset();KernelPatcher p;p.pageOK=bits&1;p.rangeOK=bits&2;start(p);
  if(bits)assert(properties.at("NRedOpenGLBackends")==bits&&Actual::reportCall&&!pending);
  else assert(properties.at("NRedOpenGLRejected")==2&&!Actual::reportCall&&freed==1);cases++;
 }
 reset();allocateOK=false;KernelPatcher p;start(p);assert(p.routes==0&&properties.at("NRedOpenGLRejected")==1);cases++;
 reset();KernelPatcher denied;denied.verifyOK=false;start(denied);
 assert(denied.routes==1&&properties.at("NRedOpenGLRejected")==3&&!Actual::reportCall&&freed==1);cases++;
 for(unsigned os:{22U,26U}){reset(os);KernelPatcher k;start(k);assert(k.routes==0&&allocated==0);cases++;}
 for(unsigned id:{0U,0x6863U,0xffff1638U}){reset(25,id);KernelPatcher k;start(k);assert(k.routes==0&&allocated==0);cases++;}
 reset();flag=false;KernelPatcher off;start(off);assert(off.routes==0&&allocated==0);cases++;
 printf("PASS %u actual OpenGL hook cases: no startup worker/file I/O; page-local matching; native ABI/status; compressed integrity; write protection; display master switch\n",cases);
}
'''.replace('BODY', body).replace('INIT', init.replace('\n{\n', '\n{\n    using namespace Actual;\n'))
    if args.stack:
        harness = harness[:harness.index('static void reset(')] + r'''
int main(){
 KernelPatcher patcher;UserOpenGL::registerKext();UserOpenGL::init(patcher);UserOpenGL::processKext(patcher,77,0x100000,0x200000);
 const auto& d=*UserOpenGLData::findDevice(25,0x1638);static std::array<uint8_t,4096> bytes;
 memcpy(bytes.data(),d.pages[0].original,4096);expectedData=bytes.data();expectedOffset=0xe000;
 int valid=0,taint=0,nx=0;Actual::validatePage(expectedVnode,expectedPager,expectedOffset,bytes.data(),&valid,&taint,&nx);flush();
}
'''
    path, exe = WORK / 'test.cpp', WORK / 'test'
    path.write_text(harness)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if args.sanitize else []
    if args.stack:
        flags = ['-Wframe-larger-than=1536', '-Wno-unused-function']
    subprocess.run(['clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', *flags,
                    '-I'+str(WORK / 'stubs'), '-I'+str(ROOT / 'NootedRed'), '-I'+str(ROOT / 'tools/fixtures'),
                    str(path), '-o', str(exe)],
                   check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
