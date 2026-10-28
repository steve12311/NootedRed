"""DVPF 跨模块共享缓存协调器：原生单次、map lease 串行/并行、容量与等待者唤醒。"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/display-coordinator'


def write_stubs():
    iokit = WORK / 'stubs/IOKit'
    iokit.mkdir(parents=True, exist_ok=True)
    (iokit / 'IOTypes.h').write_text(
        '#pragma once\n#include <cstdint>\n'
        'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n'
        'using mach_vm_address_t=UInt64;\nconstexpr int KERN_FAILURE=5;\n')
    (iokit / 'IOLocks.h').write_text(r'''
#pragma once
#include <mutex>
#include <condition_variable>
#include <IOKit/IOTypes.h>
struct IOLock { std::mutex mutex; std::condition_variable condition; };
constexpr int THREAD_UNINT=0;
inline void IOLockLock(IOLock* lock) { lock->mutex.lock(); }
inline void IOLockUnlock(IOLock* lock) { lock->mutex.unlock(); }
inline int IOLockSleep(IOLock* lock, void*, int) {
    std::unique_lock<std::mutex> guard(lock->mutex, std::adopt_lock);
    lock->condition.wait(guard);
    guard.release();
    return 0;
}
inline void IOLockWakeup(IOLock* lock, void*, bool) { lock->condition.notify_all(); }
void* current_thread();
''')
    (iokit / 'IOLib.h').write_text(r'''
#pragma once
#include <IOKit/IOLocks.h>
inline IOLock* IOLockAlloc() { return new IOLock; }
inline void IOLockFree(IOLock* lock) { delete lock; }
''')
    headers = WORK / 'stubs/Headers'
    headers.mkdir(parents=True, exist_ok=True)
    (headers / 'kern_util.hpp').write_text('#pragma once\n#include <IOKit/IOTypes.h>\n#define SYSLOG(...) ((void)0)\n')
    (headers / 'kern_patcher.hpp').write_text(r'''
#pragma once
#include <cstring>
#include <IOKit/IOTypes.h>
void* mapForTask(void*);
int nativeStub(void*, void*, int*);
class KernelPatcher {
public:
    static constexpr int KernelID = 0;
    struct RouteRequest {
        const char* symbol;
        UInt64      target;
        UInt64*     org;
        template<typename T> RouteRequest(const char* name, T value, UInt64& original)
            : symbol(name), target(reinterpret_cast<UInt64>(value)), org(&original) {}
    };
    static inline UInt64 captured{0};
    bool routeOK{true};
    UInt64 solveSymbol(int, const char* symbol) {
        return std::strcmp(symbol, "_get_task_map") == 0 ? reinterpret_cast<UInt64>(&mapForTask) : 1;
    }
    void clearError() {}
    bool routeMultipleLong(int, RouteRequest* request, int count) {
        if (!routeOK || count != 1) { return false; }
        captured = request->target;
        *request->org = reinterpret_cast<UInt64>(&nativeStub);
        return true;
    }
};
''')
    nred = WORK / 'stubs/NRed.hpp'
    nred.write_text(r'''
#pragma once
#include <map>
#include <string>
#include <IOKit/IOTypes.h>
struct NRed {
    static NRed& singleton() { static NRed instance; return instance; }
    void setProp32(const char* key, UInt32 value) { properties()[key] = value; }
    static std::map<std::string, UInt32>& properties() { static std::map<std::string, UInt32> value; return value; }
    static UInt32 value(const char* key) {
        const auto it = properties().find(key);
        return it == properties().end() ? 0 : it->second;
    }
};
''')
    kern = WORK / 'stubs/kern'
    kern.mkdir(parents=True, exist_ok=True)
    (kern / 'task.h').write_text('#pragma once\nusing task_t=void*;\ntask_t current_task();\n')
    mach = WORK / 'stubs/mach'
    mach.mkdir(parents=True, exist_ok=True)
    (mach / 'vm_map.h').write_text('#pragma once\nusing vm_map_t=void*;\n')
    sys = WORK / 'stubs/sys'
    sys.mkdir(parents=True, exist_ok=True)
    (sys / 'proc.h').write_text('#pragma once\nusing proc_t=void*;\n')


def check_integration():
    surface = (ROOT / 'NootedRed/UserSurfaceSync.cpp').read_text()
    video = (ROOT / 'NootedRed/UserVideoDecode.cpp').read_text()
    nred = (ROOT / 'NootedRed/NRed.cpp').read_text()
    assert 'UserPatchCoordinator::deferModulePatches()' in surface
    assert 'UserPatchCoordinator::deferModulePatches()' in video
    assert 'UserPatchCoordinator::registerDisplay' in surface
    assert 'UserPatchCoordinator::registerVideo' in video
    init_order = ['UserSurfaceSync::init(patcher)', 'UserOpenGL::init(patcher)',
                  'UserVideoDecode::init(patcher)', 'UserPatchCoordinator::init(patcher)']
    positions = [nred.index(value) for value in init_order]
    assert positions == sorted(positions), '协调器必须最后安装以成为最外层 Hook'


def main():
    write_stubs()
    check_integration()
    exe = WORK / 'coordinator-probe'
    flags = ['-fsanitize=thread'] if '--sanitize' in sys.argv else []
    subprocess.run(['clang++', '-std=c++23', '-O2', '-pthread', *flags, '-I', str(WORK / 'stubs'),
                    '-I', str(ROOT / 'NootedRed'), str(ROOT / 'NootedRed/UserPatchCoordinator.cpp'),
                    str(ROOT / 'tools/dvpf/coordinator_probe.cpp'), '-o', str(exe)],
                   check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
