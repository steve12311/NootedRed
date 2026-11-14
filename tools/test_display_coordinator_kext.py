"""用真实 kext 编译参数验证跨编译单元的协调器注册、状态和 Hook 执行。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/display-coordinator-kext'


def write_sources():
    stubs = WORK / 'stubs'
    files = {
        'IOKit/IOTypes.h': '''#pragma once
using UInt8=unsigned char; using UInt16=unsigned short; using UInt32=unsigned; using UInt64=unsigned long long;
using mach_vm_address_t=UInt64; constexpr int KERN_FAILURE=5;
''',
        'IOKit/IOLocks.h': '''#pragma once
struct IOLock { unsigned held; };
constexpr int THREAD_UNINT=0;
void IOLockLock(IOLock*); void IOLockUnlock(IOLock*);
int IOLockSleep(IOLock*,void*,int); void IOLockWakeup(IOLock*,void*,bool);
void* current_thread();
''',
        'IOKit/IOLib.h': '''#pragma once
#include <IOKit/IOLocks.h>
IOLock* IOLockAlloc(); void IOLockFree(IOLock*);
void* IOMalloc(unsigned long); void IOFree(void*,unsigned long);
''',
        'Headers/kern_util.hpp': '#pragma once\n#define SYSLOG(...) ((void)0)\n',
        'Headers/kern_patcher.hpp': '''#pragma once
#include <IOKit/IOTypes.h>
class KernelPatcher {
public:
    static constexpr int KernelID=0;
    struct RouteRequest {
        const char* symbol; UInt64 target; UInt64* org;
        template<typename T> RouteRequest(const char* s,T t,UInt64& o)
            :symbol(s),target(reinterpret_cast<UInt64>(t)),org(&o){}
    };
    UInt64 solveSymbol(int,const char*);
    void clearError();
    bool routeMultipleLong(int,RouteRequest*,int);
};
''',
        'NRed.hpp': '''#pragma once
#include <IOKit/IOTypes.h>
struct NRed { static NRed& singleton(); void setProp32(const char*,UInt32); };
''',
        'kern/task.h': '#pragma once\nusing task_t=void*; task_t current_task();\n',
        'mach/vm_map.h': '#pragma once\nusing vm_map_t=void*;\n',
        'sys/proc.h': '#pragma once\nusing proc_t=void*;\n',
    }
    for name, source in files.items():
        path = stubs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    for module, register in [('display', 'Display'), ('video', 'Video')]:
        (WORK / f'{module}.cpp').write_text('''#include <UserPatchCoordinator.hpp>
extern unsigned MODULECalls;
extern unsigned long long MODULEBase, MODULEProofAddress;
extern bool MODULEPatched, MODULEIdle;
static const unsigned char original[64]={}, replacement[64]={0x90,0xC3,0xCC,0xCC};
struct Site { unsigned long long address; unsigned size; const unsigned char *original, *replacement; };
struct Plan { Site values[2]; unsigned siteCount() const { return 2; } const Site& site(unsigned i) const { return values[i]; } };
static int callback(void*,void*,int result,UserPatchCoordinator::Transaction& transaction){
    ++MODULECalls;
    unsigned long long base=0; unsigned char header[UserPatchCoordinator::HeaderSize];
    MODULEBase=transaction.sharedHeader(base,header) ? base : 0;
    if(MODULEIdle)transaction.MODULE.outcome=UserPatchCoordinator::Outcome::Idle;
    else if(MODULEPatched)UserPatchCoordinator::settlePatched(transaction.MODULE,Plan{{{MODULEProofAddress,64,original,replacement},{0x20300,64,original,replacement}}});
    return result;
}
void registerREGISTERModule(){UserPatchCoordinator::registerREGISTER(callback);}
bool MODULEDefers(){return UserPatchCoordinator::deferModulePatches();}
unsigned MODULETestGroups(){return UserPatchCoordinator::requestedDisplayGroups();}
void* MODULEState(unsigned index){
    using namespace UserPatchCoordinator;
    void* addresses[]={&displayCallback(),&videoCallback(),&activeFlag(),&gateLock(),
                      &registry(),&nativeCalls(),&coordinatedRuns(),&leaseRejections(),&reportedPeak(),
                      &skippedRuns(),&reader()};
    return addresses[index];
}
'''.replace('MODULE', module).replace('REGISTER', register))
    (WORK / 'main.cpp').write_text(r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <Headers/kern_patcher.hpp>
#include <IOKit/IOLib.h>
#include <NRed.hpp>
#include <UserPatchCoordinator.hpp>

void registerDisplayModule(); void registerVideoModule();
bool displayDefers(); bool videoDefers();
unsigned displayTestGroups(); unsigned videoTestGroups();
void* displayState(unsigned); void* videoState(unsigned);
unsigned displayCalls=0,videoCalls=0;
unsigned long long displayBase=0,videoBase=0,displayProofAddress=0x20100,videoProofAddress=0x20200;
bool displayPatched=false,videoPatched=false,displayIdle=false,videoIdle=false;
static unsigned reads=0,allocations=0,frees=0;
static bool memoryOK=true,destroyOnRead=false;
static unsigned long long failRead=0;
static unsigned char memory[0x1000]{};
static unsigned long long cacheBase=0x20000;
static int readUser(unsigned long long address,void* value,unsigned long size){
    ++reads;
    if(address==failRead)return 14;
    if(destroyOnRead&&address==displayProofAddress){destroyOnRead=false;UserPatchCoordinator::forgetMap(reinterpret_cast<void*>(0x3));}
    if(address==0x1000&&size==8){std::memcpy(value,&cacheBase,8);return 0;}
    if(address<0x20000||address+size>0x20000+sizeof(memory))return 14;
    std::memcpy(value,memory+(address-0x20000),size);return 0;
}
static void patchMemory(unsigned long long address){
    const unsigned char replacement[64]={0x90,0xC3,0xCC,0xCC};
    std::memcpy(memory+(address-0x20000),replacement,64);
}
static unsigned nativeCount=0,enabled=0,skipped=0,rejected=0,routeCalls=0,lockFrees=0;
static bool lockOK=true,routeOK=true;
static int nativeResult=0;
using Hook=int (*)(void*,void*,int*);
static Hook hook=nullptr;
void* current_thread(){return reinterpret_cast<void*>(0x1);}
void* current_task(){return reinterpret_cast<void*>(0x2);}
static void* taskMap(void*){return reinterpret_cast<void*>(0x3);}
IOLock* IOLockAlloc(){static IOLock lock{};return lockOK ? &lock : nullptr;}
void IOLockFree(IOLock*){++lockFrees;}
void* IOMalloc(unsigned long size){assert(!UserPatchCoordinator::gateLock()->held);if(!memoryOK)return nullptr;void*p=std::malloc(size);if(p)++allocations;return p;}
void IOFree(void*p,unsigned long){assert(!UserPatchCoordinator::gateLock()->held);++frees;std::free(p);}
void IOLockLock(IOLock* lock){assert(!lock->held);lock->held=1;}
void IOLockUnlock(IOLock* lock){assert(lock->held);lock->held=0;}
int IOLockSleep(IOLock*,void*,int){std::abort();}
void IOLockWakeup(IOLock*,void*,bool){}
static int native(void*,void*,int* value){++nativeCount;*value=123;return nativeResult;}
UInt64 KernelPatcher::solveSymbol(int,const char* name){
    assert(std::strcmp(name,"_get_task_map")==0);
    return reinterpret_cast<UInt64>(&taskMap);
}
void KernelPatcher::clearError(){}
bool KernelPatcher::routeMultipleLong(int,RouteRequest* request,int count){
    assert(count==1 && std::strcmp(request->symbol,"_shared_region_check_np")==0);
    ++routeCalls;
    if(!routeOK)return false;
    hook=reinterpret_cast<Hook>(request->target);*request->org=reinterpret_cast<UInt64>(&native);return true;
}
NRed& NRed::singleton(){static NRed value;return value;}
void NRed::setProp32(const char* key,UInt32 value){
    if(std::strcmp(key,"NRedPatchCoordinatorEnabled")==0)enabled=value;
    if(std::strcmp(key,"NRedPatchCoordinatorSkipped")==0)skipped=value;
    if(std::strcmp(key,"NRedPatchCoordinatorRejected")==0)rejected=value;
}
int main(int argc,char** argv){
    using namespace UserPatchCoordinator;
    configureDisplay(false,25);
    assert(displayTestGroups()==0 && videoTestGroups()==0);
    configureDisplay(true,25);
    assert(displayTestGroups()==127 && videoTestGroups()==127);
    configureDisplay(true,23);
    assert(displayTestGroups()==127 && videoTestGroups()==127);
    configureDisplay(false,25);
    assert(displayTestGroups()==0 && videoTestGroups()==0);
    KernelPatcher patcher;
    if(argc==2){
        if(std::strcmp(argv[1],"none")!=0){
            if(std::strcmp(argv[1],"video-only")==0)registerVideoModule();
            else registerDisplayModule();
        }
        init(patcher);
        assert(skipped==1 && enabled==0 && routeCalls==0 && !hook && !active());
        assert(!displayDefers() && !videoDefers());
        std::puts("PASS missing participant: module hooks retain patch execution");
        return 0;
    }
    registerDisplayModule();
    init(patcher);
    assert(skipped==1 && enabled==0 && !hook);
    registerVideoModule();
    void* addresses[]={&displayCallback(),&videoCallback(),&activeFlag(),&gateLock(),
                      &registry(),&nativeCalls(),&coordinatedRuns(),&leaseRejections(),&reportedPeak(),
                      &skippedRuns(),&reader()};
    for(unsigned i=0;i<11;++i){
        if(displayState(i)!=addresses[i] || videoState(i)!=addresses[i]){
            std::fprintf(stderr,"FAIL cross-module state %u has different addresses\n",i);return 1;
        }
    }
    lockOK=false;
    init(patcher);
    assert(rejected==1 && enabled==0 && routeCalls==0 && !gateLock() && !active());
    assert(!displayDefers() && !videoDefers());
    lockOK=true;
    routeOK=false;
    init(patcher);
    assert(rejected==2 && enabled==0 && routeCalls==1 && lockFrees==1 && !gateLock() && !active());
    assert(!displayDefers() && !videoDefers());
    routeOK=true;
    init(patcher);
    assert(enabled==1 && hook && active() && displayDefers() && videoDefers());
    init(patcher);
    assert(routeCalls==2);
    UInt64 args=0x1000;int value=0;
    assert(hook(current_task(),&args,&value)==0 && value==123);
    assert(nativeCount==1 && nativeCalls()==1 && coordinatedRuns()==1);
    assert(displayCalls==1 && videoCalls==1 && registry().active==0 && registry().peak==1);
    nativeResult=37;
    assert(hook(current_task(),&args,&value)==37);
    assert(nativeCount==2 && nativeCalls()==2 && coordinatedRuns()==1);
    assert(displayCalls==1 && videoCalls==1);
    setActive(false);
    assert(!displayDefers() && !videoDefers());
    nativeResult=0;
    assert(hook(current_task(),&args,&value)==0 && value==123);
    assert(nativeCount==3 && nativeCalls()==3 && coordinatedRuns()==1);
    assert(displayCalls==1 && videoCalls==1);
    setActive(true);
    registerReader(readUser);
    displayPatched=videoPatched=true;
    patchMemory(displayProofAddress);patchMemory(videoProofAddress);patchMemory(0x20300);
    reads=0;
    assert(hook(current_task(),&args,&value)==0);
    assert(displayCalls==2 && videoCalls==2 && coordinatedRuns()==2 && skippedRuns()==0);
    assert(displayBase==cacheBase && videoBase==cacheBase && reads==2);
    reads=0;
    assert(hook(current_task(),&args,&value)==0 && value==123);
    assert(displayCalls==2 && videoCalls==2 && skippedRuns()==1 && reads==6);
    // 第二站点的最后一字节也必须参与证明，不能只覆盖首站点或前 16 字节。
    memory[0x33f]=1;
    assert(hook(current_task(),&args,&value)==0);
    assert(displayCalls==3 && videoCalls==3 && skippedRuns()==1);
    patchMemory(0x20300);
    assert(hook(current_task(),&args,&value)==0 && skippedRuns()==2);
    // 第一站点的尾部被修改，或任一站点读取失败，都回到完整事务。
    memory[0x13f]=1;
    assert(hook(current_task(),&args,&value)==0 && displayCalls==4 && videoCalls==4);
    patchMemory(displayProofAddress);failRead=videoProofAddress;
    assert(hook(current_task(),&args,&value)==0 && displayCalls==5 && videoCalls==5);
    failRead=0;
    // 同一基址但缓存 UUID 变化：不能复用旧证明。
    memory[88]^=1;
    assert(hook(current_task(),&args,&value)==0 && displayCalls==6 && videoCalls==6);
    forgetMap(reinterpret_cast<void*>(0x3));
    assert(hook(current_task(),&args,&value)==0 && displayCalls==7 && videoCalls==7);
    // 锁外 copyin 中清除表项，快照仍由引用计数保护；下一次调用必须重新核验。
    destroyOnRead=true;
    assert(hook(current_task(),&args,&value)==0 && displayCalls==7 && videoCalls==7);
    assert(hook(current_task(),&args,&value)==0 && displayCalls==8 && videoCalls==8);
    // 分配失败时不保存局部证明，持续回退；一侧 Pending 也不能跳过。
    forgetMap(reinterpret_cast<void*>(0x3));memoryOK=false;
    assert(hook(current_task(),&args,&value)==0);
    assert(hook(current_task(),&args,&value)==0 && displayCalls==10 && videoCalls==10);
    memoryOK=true;videoPatched=false;
    assert(hook(current_task(),&args,&value)==0);
    assert(hook(current_task(),&args,&value)==0 && displayCalls==12 && videoCalls==12);
    videoPatched=true;
    assert(hook(current_task(),&args,&value)==0 && displayCalls==13 && videoCalls==13);
    // 基址改变、头读取失败：模块回到自身读取路径。
    cacheBase+=0x1000;
    assert(hook(current_task(),&args,&value)==0 && displayCalls==14 && videoCalls==14 && displayBase==0);
    cacheBase-=0x1000;
    // 65 个 map 使 64 项表发生淘汰；清理后所有分配均释放。
    for(unsigned i=0;i<65;i++)coordinate(current_task(),&args,0,reinterpret_cast<void*>(0x100+i));
    for(unsigned i=0;i<65;i++)forgetMap(reinterpret_cast<void*>(0x100+i));
    forgetMap(reinterpret_cast<void*>(0x3));
    assert(allocations==frees && registry().active==0);
    // 一侧由配置停用时，只核验另一侧；两侧均 Idle 时不记录可跳过的结果。
    const auto idleStart=displayCalls;
    videoIdle=true;
    assert(hook(current_task(),&args,&value)==0);
    assert(hook(current_task(),&args,&value)==0 && displayCalls==idleStart+1 && videoCalls==idleStart+1);
    forgetMap(reinterpret_cast<void*>(0x3));displayIdle=true;
    assert(hook(current_task(),&args,&value)==0);
    assert(hook(current_task(),&args,&value)==0 && displayCalls==idleStart+3 && videoCalls==idleStart+3);
    displayIdle=videoIdle=false;
    assert(allocations==frees);
    // 所有站点的原始字节与替换字节相同、空计划、超大补丁和越界地址都不能生成证明。
    {
        unsigned char original[512]{},replacement[512]{};replacement[511]=1;
        struct Site{unsigned long long address;unsigned size;const unsigned char*original,*replacement;};
        struct Plan{Site value;unsigned count;unsigned siteCount()const{return count;}const Site&site(unsigned)const{return value;}};
        Transaction tx;
        auto& part=tx.display;
        Plan p{{0x30000,512,original,replacement},1};
        settlePatched(part,p);assert(part.outcome==Outcome::Patched && part.proof->siteCount==1);
        replacement[511]=2;assert(part.proof->sites[0].bytes[511]==1);
        p.value.replacement=original;settlePatched(part,p);assert(part.outcome==Outcome::Pending&&!part.proof);
        p.count=0;settlePatched(part,p);assert(!part.proof);
        p.count=MaxProofSites+1;settlePatched(part,p);assert(!part.proof);
        p.count=1;p.value.size=513;settlePatched(part,p);assert(!part.proof);
        p.value.size=512;p.value.address=UserSharedCache::UserLimit-4;settlePatched(part,p);assert(!part.proof);
    }
    assert(allocations==frees);
    clearDisplay();clearVideo();
    assert(!displayCallback() && !videoCallback());
    std::puts("PASS cross-module coordinator: unique state, registration, defer, native error, single execution, shared header, full multi-site proof, late-byte mismatch, UUID change, read/allocation failure, snapshot lifetime and eviction");
}
''')


def main():
    write_sources()
    for mode, flags in [('normal', ['-O2']),
                        ('kext-debug', ['-O0', '-mkernel', '-fapple-kext', '-fvisibility-inlines-hidden']),
                        ('kext-release', ['-O3', '-mkernel', '-fapple-kext', '-fvisibility-inlines-hidden']),
                        ('sanitized', ['-O1', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all'])]:
        objects = []
        for name, path in [('coordinator', ROOT / 'NootedRed/UserPatchCoordinator.cpp'),
                           ('display', WORK / 'display.cpp'), ('video', WORK / 'video.cpp')]:
            if mode == 'sanitized' and name == 'coordinator':
                continue
            obj = WORK / f'{mode}-{name}.o'
            subprocess.run(['xcrun', 'clang++', '-arch', 'x86_64', '-std=c++23', '-fno-exceptions', '-fno-rtti',
                            *flags, '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                            '-c', str(path), '-o', str(obj)], check=True, timeout=60)
            objects.append(str(obj))
        exe = WORK / mode
        main_source = WORK / 'main.cpp'
        if mode == 'sanitized':
            # 同一翻译单元可使用 Hook 的私有参数类型，让 UBSan 核验准确函数签名。
            checked = '#include "' + str(ROOT / 'NootedRed/UserPatchCoordinator.cpp') + '"\n' + main_source.read_text()
            checked = checked.replace('using Hook=int (*)(void*,void*,int*);',
                                      'using Hook=int (*)(void*,SharedRegionCheckArgs*,int*);')
            checked = checked.replace('static int native(void*,void*,int* value)',
                                      'static int native(void*,SharedRegionCheckArgs*,int* value)')
            checked = checked.replace('UInt64 args=0x1000;int value=0;',
                                      'SharedRegionCheckArgs args{0x1000};int value=0;')
            main_source = WORK / 'sanitized-main.cpp'
            main_source.write_text(checked)
        subprocess.run(['xcrun', 'clang++', '-arch', 'x86_64', '-std=c++23',
                        *(['-fsanitize=address,undefined', '-fno-sanitize-recover=all'] if mode == 'sanitized' else []),
                        '-I' + str(WORK / 'stubs'),
                        '-I' + str(ROOT / 'NootedRed'), str(main_source), *objects,
                        '-o', str(exe)], check=True, timeout=60)
        print(f'{mode}:', flush=True)
        subprocess.run([str(exe)], check=True, timeout=60)
        for scenario in ['none', 'display-only', 'video-only']:
            subprocess.run([str(exe), scenario], check=True, timeout=60)


if __name__ == '__main__':
    main()
