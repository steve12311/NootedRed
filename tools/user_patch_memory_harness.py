"""将公共页面工具注入已有 Mach API 模拟环境，执行生产实现。"""
from pathlib import Path
import re


def with_memory_helpers(body):
    if "UserPatchMemory::" not in body:
        return body
    header = Path(__file__).resolve().parents[1] / "NootedRed/UserPatchMemory.hpp"
    helpers = re.sub(r"^#(?:include|pragma).*\n", "", header.read_text(), flags=re.MULTILINE)
    return helpers + "\n" + body


INACTIVE_DISPLAY_TIMING = """
namespace UserPhaseTiming {
enum class Phase { DisplayResolve, DisplayPrepare, DisplayWrite };
inline void setEnabled(bool) {}
struct Timer { explicit Timer(Phase) {} void stop() {} };
}
"""


def with_inactive_coordinator(preamble, stubs, existing_locks=False):
    """旧 Hook 入口测试也使用生产事务类型；协调器调度由独立的跨模块测试覆盖。"""
    iokit = stubs / 'IOKit'
    (iokit / 'IOLocks.h').write_text('#pragma once\n' if existing_locks else '''#pragma once
#include <mutex>
struct IOLock { std::mutex mutex; };
inline IOLock* IOLockAlloc(){return new IOLock;}
inline void IOLockFree(IOLock* p){delete p;}
inline void IOLockLock(IOLock* p){p->mutex.lock();}
inline void IOLockUnlock(IOLock* p){p->mutex.unlock();}
''')
    (iokit / 'IOLib.h').write_text('#pragma once\n#include <IOKit/IOLocks.h>\n')
    header = (Path(__file__).resolve().parents[1] / 'NootedRed/UserPatchCoordinator.hpp').read_text()
    transaction = header[header.index('    constexpr UInt32 MaxMapSlots'):header.index('    // 回调只接收')]
    return preamble + '\n#include <UserRejectedCaches.hpp>\nnamespace UserPatchCoordinator {\n' + transaction + '''
inline Proof* allocateProof(){return new Proof{};}
inline void retainProof(Proof* p){if(p)++p->references;}
inline void releaseProof(Proof* p){if(p&&p->references<=1)delete p;else if(p)--p->references;}
inline bool processAllowed(void*){return true;}
}\n'''
