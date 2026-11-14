"""Fase 2: tabella delle cache rifiutate (conferma, accettazione, ricontrollo periodico, evizione)."""
from pathlib import Path
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/rejected-caches'


def main():
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;\n')
    (stubs / 'IOLocks.h').write_text('#pragma once\n#include <mutex>\nstruct IOLock{std::mutex m;};\n'
                                     'inline void IOLockLock(IOLock* l){l->m.lock();}\n'
                                     'inline void IOLockUnlock(IOLock* l){l->m.unlock();}\n')
    (stubs / 'IOLib.h').write_text('#pragma once\n#include <IOKit/IOLocks.h>\n'
                                   'inline bool lockOK=true;\n'
                                   'inline IOLock* IOLockAlloc(){return lockOK?new IOLock:nullptr;}\n')
    source = WORK / 'test.cpp'
    source.write_text(r'''
#include <cassert>
#include <cstdio>
#include <thread>
#include <vector>
#include <UserRejectedCaches.hpp>
using namespace UserRejectedCaches;
static void uuid(UInt8* out, UInt8 tag){for(UInt32 i=0;i<UUIDSize;++i)out[i]=UInt8(tag+i);}
int main(){
    UInt8 kit[16],main_[16],other[16];uuid(kit,0xD0);uuid(main_,0x31);uuid(other,0x70);
    {   // senza lock: mai saltare
        lockOK=false;Table t;assert(!t.init());t.reject(kit);t.reject(kit);assert(!t.shouldSkip(kit));lockOK=true;
    }
    Table t;assert(t.init());
    assert(!t.shouldSkip(kit));
    t.reject(kit);assert(!t.shouldSkip(kit));            // un solo rifiuto non basta
    t.reject(kit);
    unsigned skipped=0,rechecks=0;
    for(unsigned i=1;i<=128;++i){if(t.shouldSkip(kit))++skipped;else ++rechecks;}
    assert(skipped==126&&rechecks==2);                    // ricontrollo ogni 64
    assert(!t.shouldSkip(main_));
    t.accept(main_);t.reject(main_);t.reject(main_);t.reject(main_);
    assert(!t.shouldSkip(main_));                         // già accettata: mai ricordata
    t.accept(kit);assert(!t.shouldSkip(kit));             // accettazione toglie il rifiuto
    t.reject(kit);t.reject(kit);assert(!t.shouldSkip(kit)); // e impedisce di ricordarla di nuovo
    // evizione: 4 slot, il quinto UUID sostituisce il più vecchio
    UInt8 ids[5][16];for(int i=0;i<5;++i){uuid(ids[i],UInt8(0x80+i*0x10));t.reject(ids[i]);t.reject(ids[i]);}
    assert(t.shouldSkip(ids[4]));
    unsigned remembered=0;for(int i=0;i<5;++i)if(t.shouldSkip(ids[i]))++remembered;
    assert(remembered==4);
    // concorrenza
    Table c;assert(c.init());c.reject(other);c.reject(other);
    std::vector<std::thread> th;std::atomic<unsigned> s{0};
    for(int i=0;i<8;++i)th.emplace_back([&]{for(int j=0;j<1000;++j)if(c.shouldSkip(other))++s;});
    for(auto& x:th)x.join();
    assert(s==8000-8000/64);
    puts("PASS rejected caches: confirm, accept wins, periodic recheck, eviction, concurrency");
}
''')
    exe = WORK / 'test'
    compiler = os.environ.get('CXX') or shutil.which('clang++')
    subprocess.run([compiler, '-std=c++20', '-O2', '-pthread', '-fsanitize=thread', '-include', 'atomic',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'), str(source), '-o', str(exe)],
                   check=True, timeout=120)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
