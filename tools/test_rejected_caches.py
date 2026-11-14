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
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
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
static UserSharedCache::Input missingInput(){
    static const UserSharedCache::ImageReference refs[]={{"/required",10}};
    UserSharedCache::Input input{nullptr,{}};
    input.imageRefs=refs;input.imageCount=1;input.tableRead=true;
    return input;
}
static void uuid(UInt8* out, UInt8 tag){for(UInt32 i=0;i<UUIDSize;++i)out[i]=UInt8(tag+i);}
int main(){
    UInt8 kit[16],main_[16],other[16];uuid(kit,0xD0);uuid(main_,0x31);uuid(other,0x70);
    {   // senza lock: mai saltare
        lockOK=false;Table t;assert(!t.init());t.rejectMissingImages(kit,missingInput());t.rejectMissingImages(kit,missingInput());assert(!t.shouldSkip(kit));lockOK=true;
    }
    Table t;assert(t.init());
    assert(!t.shouldSkip(kit));
    // 读取失败、未完成元数据和已有全部目标镜像的代码核验失败不污染拒绝表。
    for(unsigned mode=0;mode<3;mode++){
        auto input=missingInput();
        if(mode==0)input.failed=true;
        if(mode==1)input.tableRead=false;
        if(mode==2)input.imageAddresses[0]=0x100000;
        for(unsigned i=0;i<4;i++)t.rejectMissingImages(kit,input);
        assert(!t.shouldSkip(kit));
    }
    t.rejectMissingImages(kit,missingInput());assert(!t.shouldSkip(kit));            // un solo rifiuto non basta
    t.rejectMissingImages(kit,missingInput());
    unsigned skipped=0,rechecks=0;
    for(unsigned i=1;i<=128;++i){if(t.shouldSkip(kit))++skipped;else ++rechecks;}
    assert(skipped==126&&rechecks==2);                    // ricontrollo ogni 64
    assert(!t.shouldSkip(main_));
    t.accept(main_);t.rejectMissingImages(main_,missingInput());t.rejectMissingImages(main_,missingInput());t.rejectMissingImages(main_,missingInput());
    assert(!t.shouldSkip(main_));                         // già accettata: mai ricordata
    t.accept(kit);assert(!t.shouldSkip(kit));             // accettazione toglie il rifiuto
    t.rejectMissingImages(kit,missingInput());t.rejectMissingImages(kit,missingInput());assert(!t.shouldSkip(kit)); // e impedisce di ricordarla di nuovo
    // evizione: 4 slot, il quinto UUID sostituisce il più vecchio
    UInt8 ids[5][16];for(int i=0;i<5;++i){uuid(ids[i],UInt8(0x80+i*0x10));t.rejectMissingImages(ids[i],missingInput());t.rejectMissingImages(ids[i],missingInput());}
    assert(t.shouldSkip(ids[4]));
    unsigned remembered=0;for(int i=0;i<5;++i)if(t.shouldSkip(ids[i]))++remembered;
    assert(remembered==4);
    // concorrenza
    Table c;assert(c.init());c.rejectMissingImages(other,missingInput());c.rejectMissingImages(other,missingInput());
    std::vector<std::thread> th;std::atomic<unsigned> s{0};
    for(int i=0;i<8;++i)th.emplace_back([&]{for(int j=0;j<1000;++j)if(c.shouldSkip(other))++s;});
    for(auto& x:th)x.join();
    assert(s==8000-8000/64);
    puts("PASS rejected caches: transient rejection ignored, missing images confirm, accept wins, periodic recheck, eviction, concurrency");
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
