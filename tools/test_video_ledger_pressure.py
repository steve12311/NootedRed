"""执行生产视频账本路径，覆盖大量存活 map、隔离状态及分配失败。"""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/video-ledger-pressure'


def main():
    stub = WORK / 'stubs/IOKit/IOTypes.h'
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text('#pragma once\n#include <cstdint>\n'
                    'using UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source = (ROOT / 'NootedRed/UserVideoDecode.cpp').read_text()
    declaration = re.search(r'    (?:VPF::CapabilityLedger|VideoLedger)\s+videoLedger;', source)[0]
    if 'class VideoLedger' in source:
        declaration = source[source.index('    class VideoLedger'):source.index(declaration)] + declaration
    helpers = source[source.index('    VPF::CapabilityState ledgerState('):source.index('    // 统一执行器')]
    harness = r'''
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <VPF/CapabilityState.hpp>
static bool failAllocation=false;
static unsigned allocations=0,frees=0;
template<class T> static T* allocate(size_t n) {
    if(failAllocation)return nullptr;
    auto* p=static_cast<T*>(std::malloc(sizeof(T)*n));
    if(p){++allocations;std::memset(p,0xA5,sizeof(T)*n);}return p;
}
#define IONew(T,N) allocate<T>(N)
#define IODelete(P,T,N) do{++frees;std::free(P);}while(false)
using vm_map_t=void*;
using IOLock=std::mutex;
static IOLock mutex;
static IOLock* guardLock=&mutex;
static void IOLockLock(IOLock* p){p->lock();}
static void IOLockUnlock(IOLock* p){p->unlock();}
struct NRed {
    std::map<std::string,UInt32> props;
    static NRed& singleton(){static NRed n;return n;}
    void setProp32(const char* key,UInt32 value){props[key]=value;}
};
static struct{UInt64 ledgerState;} published{};
static void publish(UInt64&,const char* key,UInt32 value){NRed::singleton().setProp32(key,value);}
DECLARATION
HELPERS
int main(){
    constexpr auto feature=VPF::FeatureId::Vcn2Decode;
    constexpr UInt64 count=1024;
    for(UInt64 i=1;i<=count;++i){
        if(!prepareLedger(i)){std::fprintf(stderr,"rejected live map %llu\n",i);return 1;}
        installedLedger(i);
    }
    constexpr UInt64 decoder=count+1;
    assert(prepareLedger(decoder));installedLedger(decoder);observeLedger(decoder);
    assert(ledgerState(decoder)==VPF::CapabilityState::RuntimeObserved);
    // 大量活跃 map 存在时也不能丢弃隔离状态。
    removeLedgerMap(reinterpret_cast<void*>(1));
    assert(prepareLedger(1));assert(markLedger(1,VPF::CapabilityState::Installing));
    markFailure(1,false);assert(!prepareLedger(1));
    for(UInt64 i=2;i<=count;++i)assert(ledgerState(i)==VPF::CapabilityState::Installed);
    failAllocation=true;
    assert(!prepareLedger(count+2));assert(ledgerState(count+2)==VPF::CapabilityState::Disabled);
    assert(!prepareLedger(1));assert(prepareLedger(decoder));
    failAllocation=false;
    assert(prepareLedger(count+2));
    removeLedgerMap(reinterpret_cast<void*>(1));assert(prepareLedger(1));
    assert(!videoLedger.set(0,feature,VPF::CapabilityState::Eligible));
    assert(!videoLedger.set(count+3,feature,VPF::CapabilityState::Installed));
    for(UInt64 i=1;i<=count+2;++i)removeLedgerMap(reinterpret_cast<void*>(i));
    assert(allocations==frees);
    // 重复分配、销毁和地址复用不保留历史条目。
    for(UInt64 i=0;i<2048;++i){assert(prepareLedger(1));removeLedgerMap(reinterpret_cast<void*>(1));}
    assert(allocations==frees);
    std::puts("PASS video ledger: 1024 live maps + decoder, quarantine, allocation failure, cleanup and map reuse");
}
'''.replace('DECLARATION', declaration).replace('HELPERS', helpers)
    path, executable = WORK / 'test.cpp', WORK / 'test'
    path.write_text(harness)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    str(path), '-o', str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == '__main__':
    main()
