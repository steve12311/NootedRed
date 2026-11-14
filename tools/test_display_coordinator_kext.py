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
extern bool MODULEPatched;
static const unsigned char original[4]={0x90,0x90,0x90,0x90}, replacement[4]={0x90,0xC3,0xCC,0xCC};
struct Site { unsigned long long address; unsigned size; const unsigned char *original, *replacement; };
struct Plan { Site value; unsigned siteCount() const { return 1; } const Site& site(unsigned) const { return value; } };
static int callback(void*,void*,int result,UserPatchCoordinator::Transaction& transaction){
    ++MODULECalls;
    unsigned long long base=0; unsigned char header[UserPatchCoordinator::HeaderSize];
    MODULEBase=transaction.sharedHeader(base,header) ? base : 0;
    if(MODULEPatched)UserPatchCoordinator::settlePatched(transaction.MODULE,Plan{{MODULEProofAddress,4,original,replacement}});
    return result;
}
void registerREGISTERModule(){UserPatchCoordinator::registerREGISTER(callback);}
bool MODULEDefers(){return UserPatchCoordinator::deferModulePatches();}
unsigned MODULEGroups(){return UserPatchCoordinator::requestedDisplayGroups();}
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
unsigned displayGroups(); unsigned videoGroups();
void* displayState(unsigned); void* videoState(unsigned);
unsigned displayCalls=0,videoCalls=0;
unsigned long long displayBase=0,videoBase=0,displayProofAddress=0x20010,videoProofAddress=0x20020;
bool displayPatched=false,videoPatched=false;
static unsigned reads=0;
static unsigned char memory[0x100]{};
static unsigned long long cacheBase=0x20000;
static int readUser(unsigned long long address,void* value,unsigned long size){
    ++reads;
    if(address==0x1000&&size==8){std::memcpy(value,&cacheBase,8);return 0;}
    if(address<0x20000||address+size>0x20000+sizeof(memory))return 14;
    std::memcpy(value,memory+(address-0x20000),size);return 0;
}
static void patchMemory(unsigned long long address){
    const unsigned char replacement[4]={0x90,0xC3,0xCC,0xCC};
    std::memcpy(memory+(address-0x20000),replacement,4);
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
    assert(displayGroups()==0 && videoGroups()==0);
    configureDisplay(true,25);
    assert(displayGroups()==127 && videoGroups()==127);
    configureDisplay(true,23);
    assert(displayGroups()==127 && videoGroups()==127);
    configureDisplay(false,25);
    assert(displayGroups()==0 && videoGroups()==0);
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
    // 缓存头只读一次并传给两个参与者；两侧完成且 proof 仍在内存中时，第二次调用跳过搜索。
    setActive(true);
    registerReader(readUser);
    assert(reader()==readUser);
    displayPatched=videoPatched=true;
    patchMemory(displayProofAddress);patchMemory(videoProofAddress);
    reads=0;
    assert(hook(current_task(),&args,&value)==0);
    assert(displayCalls==2 && videoCalls==2 && coordinatedRuns()==2 && skippedRuns()==0);
    assert(displayBase==cacheBase && videoBase==cacheBase && reads==2);
    reads=0;
    assert(hook(current_task(),&args,&value)==0 && value==123);
    assert(displayCalls==2 && videoCalls==2 && skippedRuns()==1 && reads==4);
    // proof 消失（例如 map 地址被新进程复用）：回到完整搜索。
    std::memset(memory+(videoProofAddress-0x20000),0x90,4);
    assert(hook(current_task(),&args,&value)==0);
    assert(displayCalls==3 && videoCalls==3 && skippedRuns()==1);
    // 一侧未完成：不记录，下次仍完整搜索。
    videoPatched=false;
    assert(hook(current_task(),&args,&value)==0);
    assert(hook(current_task(),&args,&value)==0);
    assert(displayCalls==5 && videoCalls==5 && skippedRuns()==1);
    // 重新完成后再跳过；forgetMap 清除后必须重新搜索。
    videoPatched=true;patchMemory(videoProofAddress);
    assert(hook(current_task(),&args,&value)==0);
    assert(hook(current_task(),&args,&value)==0);
    assert(displayCalls==6 && videoCalls==6 && skippedRuns()==2);
    forgetMap(reinterpret_cast<void*>(0x3));
    assert(hook(current_task(),&args,&value)==0);
    assert(displayCalls==7 && videoCalls==7 && skippedRuns()==2);
    // 缓存基址变化：不跳过。
    cacheBase=0x20000+0x1000;
    assert(hook(current_task(),&args,&value)==0);
    assert(displayCalls==8 && videoCalls==8 && displayBase==0 && skippedRuns()==2);
    cacheBase=0x20000;
    assert(registry().active==0);
    clearDisplay();clearVideo();
    assert(!displayCallback() && !videoCallback());
    std::puts("PASS cross-module coordinator: unique state, registration, defer, native error, single execution, shared header and proven skip");
}
''')


def main():
    write_sources()
    for mode, flags in [('normal', ['-O2']),
                        ('kext-debug', ['-O0', '-mkernel', '-fapple-kext', '-fvisibility-inlines-hidden']),
                        ('kext-release', ['-O3', '-mkernel', '-fapple-kext', '-fvisibility-inlines-hidden'])]:
        objects = []
        for name, path in [('coordinator', ROOT / 'NootedRed/UserPatchCoordinator.cpp'),
                           ('display', WORK / 'display.cpp'), ('video', WORK / 'video.cpp')]:
            obj = WORK / f'{mode}-{name}.o'
            subprocess.run(['xcrun', 'clang++', '-arch', 'x86_64', '-std=c++23', '-fno-exceptions', '-fno-rtti',
                            *flags, '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                            '-c', str(path), '-o', str(obj)], check=True, timeout=60)
            objects.append(str(obj))
        exe = WORK / mode
        subprocess.run(['xcrun', 'clang++', '-arch', 'x86_64', '-std=c++23', '-I' + str(WORK / 'stubs'),
                        '-I' + str(ROOT / 'NootedRed'), str(WORK / 'main.cpp'), *objects,
                        '-o', str(exe)], check=True, timeout=60)
        print(f'{mode}:', flush=True)
        subprocess.run([str(exe)], check=True, timeout=60)
        for scenario in ['none', 'display-only', 'video-only']:
            subprocess.run([str(exe), scenario], check=True, timeout=60)


if __name__ == '__main__':
    main()
