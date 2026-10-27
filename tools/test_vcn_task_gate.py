"""执行真实共享缓存钩子，验证独立进程不被另一进程的扫描阻塞。"""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vcn-task-gate'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect-global-stall', action='store_true')
    parser.add_argument('--baseline-ref', help='使用指定本地提交中的真实钩子复现旧行为')
    parser.add_argument('--sanitize', choices=['address', 'thread'], default='address')
    args = parser.parse_args()
    text = subprocess.check_output(['git', 'show', args.baseline_ref + ':NootedRed/UserVideoDecode.cpp'],
                                   cwd=ROOT, text=True, timeout=10) if args.baseline_ref else (
                                       ROOT / 'NootedRed/UserVideoDecode.cpp').read_text()
    start = text.index('    UInt32 hookTraceCount{0};')
    wrapper = text[start:text.index('}    // namespace', start)]
    helper = ROOT / 'NootedRed/UserPatchMapGate.hpp'
    use_helper = helper.exists() and not args.baseline_ref
    source = r'''
#include <cassert>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdint>
#include <memory>
#include <map>
#include <mutex>
#include <thread>
#include <vector>
using UInt32=uint32_t;using task_t=void*;using proc_t=void*;using IOLock=std::mutex;
constexpr int THREAD_UNINT=0;
static std::map<void*,std::condition_variable_any> gateEvents;
static void IOLockLock(IOLock* p){p->lock();}
static void IOLockUnlock(IOLock* p){p->unlock();}
static int IOLockSleep(IOLock* p,void* event,int){auto& changed=gateEvents[event];std::unique_lock held(*p,std::adopt_lock);changed.wait(held);held.release();return 0;}
static void IOLockWakeup(IOLock*,void* event,bool){auto found=gateEvents.find(event);if(found!=gateEvents.end())found->second.notify_all();}
HELPER
static IOLock lock;static IOLock* guardLock=&lock;
REGISTRY
static void recordTaskGatePeak(){}
static thread_local task_t task=nullptr;
static task_t current_task(){return task;}
static void* mapForTask(task_t task){return task==reinterpret_cast<void*>(3)?reinterpret_cast<void*>(1):task;}
static auto getTaskMap=mapForTask;
struct SharedRegionCheckArgs {uint64_t startAddress;};
using SharedRegionCheck=int(*)(proc_t,SharedRegionCheckArgs*,int*);
static uintptr_t originalCheck=0;
enum class Stage {Called,OriginalError};static void stage(Stage){}
static int proc_pid(proc_t p){return int(reinterpret_cast<uintptr_t>(p));}
#define SYSLOG(...) ((void)0)
static std::mutex state;static std::condition_variable changed;
static bool aEntered=false,bEntered=false,releaseA=false;
static std::atomic<unsigned> nativeCalls{0};static unsigned aInside=0,aPeak=0;
static int original(proc_t,SharedRegionCheckArgs*,int* out){nativeCalls++;*out=123;return 0;}
static int patchSharedRegion(proc_t,SharedRegionCheckArgs*,int result){
 std::unique_lock held(state);
 if(getTaskMap(task)==reinterpret_cast<void*>(1)){
  aPeak=std::max(aPeak,++aInside);aEntered=true;changed.notify_all();
  changed.wait(held,[]{return releaseA;});aInside--;
 }else{bEntered=true;changed.notify_all();}
 return result;
}
WRAPPER
static void run(unsigned key){task=reinterpret_cast<void*>(uintptr_t(key));SharedRegionCheckArgs args{0x1000};int out=0;
 assert(wrappedSharedRegionCheck(task,&args,&out)==0&&out==123);}
int main(){
 originalCheck=reinterpret_cast<uintptr_t>(original);
 std::thread first([]{run(1);});
 {std::unique_lock held(state);assert(changed.wait_for(held,std::chrono::seconds(2),[]{return aEntered;}));}
 std::thread duplicate([]{run(3);}),independent([]{run(2);});
 bool independentProgress;
 {std::unique_lock held(state);independentProgress=changed.wait_for(held,std::chrono::milliseconds(500),[]{return bEntered;});releaseA=true;changed.notify_all();}
 first.join();duplicate.join();independent.join();
 assert(nativeCalls==3&&aPeak==1);
 if(EXPECT_STALL){assert(!independentProgress);puts("PASS baseline reproduces cross-process startup blocking while another task resolves cache pages");return 0;}
 assert(independentProgress);
 STRESS
 puts("PASS independent-map progress, shared-map serialization across tasks, bounded saturation/wakeup, 1600 concurrent leases and native-result preservation");
}
'''
    stress = r'''
 UserPatchMapGate::Registry<4> registry;
 using Lease=UserPatchMapGate::Lease<4>;
 std::vector<std::unique_ptr<Lease>> leases;
 for(unsigned i=0;i<4;i++)leases.emplace_back(new Lease(registry,&lock,reinterpret_cast<void*>(uintptr_t(i+10))));
 std::atomic<bool> waiting{false},entered{false};
 std::thread fifth([&]{waiting=true;Lease lease(registry,&lock,reinterpret_cast<void*>(20));assert(lease);entered=true;});
 while(!waiting)std::this_thread::yield();
 {std::lock_guard held(lock);assert(!entered);}
 leases[0].reset();fifth.join();assert(entered);leases.clear();
 std::atomic<unsigned> active[4]{};std::vector<std::thread> threads;
 for(unsigned n=0;n<16;n++)threads.emplace_back([&,n]{for(unsigned i=0;i<100;i++){
  unsigned key=n%4;Lease lease(registry,&lock,reinterpret_cast<void*>(uintptr_t(key+1)));assert(lease);
  assert(active[key].fetch_add(1)==0);std::this_thread::yield();assert(active[key].fetch_sub(1)==1);
 }});
 for(auto& t:threads)t.join();
 for(auto key:registry.keys)assert(key==nullptr);
 assert(registry.active==0&&registry.peak==4);
'''
    source = source.replace('WRAPPER', wrapper).replace('EXPECT_STALL', str(int(args.expect_global_stall)))
    source = source.replace('HELPER', '#include <UserPatchMapGate.hpp>' if use_helper else '')
    source = source.replace('REGISTRY', 'static UserPatchMapGate::Registry<64> taskPatchRegistry;' if use_helper else '')
    source = source.replace('STRESS', stress if use_helper else '')
    WORK.mkdir(parents=True, exist_ok=True)
    path, exe = WORK / ('test-' + args.sanitize + '.cpp'), WORK / ('test-' + args.sanitize)
    path.write_text(source)
    sanitize = '-fsanitize=address,undefined' if args.sanitize == 'address' else '-fsanitize=thread'
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', sanitize,
                    '-fno-omit-frame-pointer', '-I' + str(ROOT / 'NootedRed'), str(path), '-o', str(exe)],
                   check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=10)


if __name__ == '__main__':
    main()
