"""执行实际 ABI 核验函数，覆盖虚表地址点、槽身份、完整 getter 和读取边界。"""
from pathlib import Path
import argparse
import subprocess
from test_vcn_native_load import function

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vcn/kernel-abi'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\n'
                                   'using UInt8=uint8_t;using UInt32=uint32_t;\n')
    source = (ROOT / 'NootedRed/VCN.cpp').read_text()
    process = function(source, 'void VCN::processKext(')
    assert process.index('verifyKernelABI(') < process.index('routeMultiple(')
    functions = 'template<typename T>\n' + function(source, 'bool resolve(')
    functions += '\n' + function(source, 'bool verifyKernelABI(')
    fixture = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <VCNKernelData.hpp>
using mach_vm_address_t=uintptr_t;
#define SYSLOG(...) ((void)0)
template<typename T,size_t N>constexpr size_t arrsize(const T(&)[N]){return N;}
struct KernelPatcher {
    std::map<std::string,uintptr_t> symbols;
    uintptr_t solveSymbol(size_t,const char* name,uintptr_t,size_t,bool){return symbols[name];}
    void clearError(){}
};
FUNCTIONS
int main(){
    alignas(8) std::array<uint8_t,8192> memory{};
    const auto base=reinterpret_cast<uintptr_t>(memory.data());
    KernelPatcher patcher;
    for(bool old:{false,true}){
        const auto* guards=old?VCNKernelData::Guards5000:VCNKernelData::Guards6000;
        const auto* table=old?VCNKernelData::Table5000:VCNKernelData::Table6000;
        const auto reset=[&]{
            memory.fill(0);patcher.symbols.clear();patcher.symbols[table]=base+256;
            for(unsigned i=0;i<6;i++){
                const auto& guard=guards[i];const auto target=base+2048+i*64;
                memcpy(memory.data()+256+16+guard.slot,&target,8);
                if(guard.target){patcher.symbols[guard.target]=target;}
                else{memcpy(memory.data()+2048+i*64,guard.code,guard.size);}
            }
        };
        reset();const auto original=memory;
        assert(verifyKernelABI(patcher,1,base,memory.size(),old)&&memory==original);
        for(unsigned i=0;i<6;i++){
            reset();uintptr_t target=base+memory.size();memcpy(memory.data()+256+16+guards[i].slot,&target,8);
            assert(!verifyKernelABI(patcher,1,base,memory.size(),old));
            reset();target=base-1;memcpy(memory.data()+256+16+guards[i].slot,&target,8);
            assert(!verifyKernelABI(patcher,1,base,memory.size(),old));
            if(guards[i].target){
                reset();patcher.symbols[guards[i].target]++;
                assert(!verifyKernelABI(patcher,1,base,memory.size(),old));
                reset();patcher.symbols.erase(guards[i].target);
                assert(!verifyKernelABI(patcher,1,base,memory.size(),old));
            }else{
                for(unsigned byte:{0U,guards[i].size-1}){
                    reset();memory[2048+i*64+byte]^=1;
                    assert(!verifyKernelABI(patcher,1,base,memory.size(),old));
                }
                reset();target=base+memory.size()-guards[i].size+1;
                memcpy(memory.data()+256+16+guards[i].slot,&target,8);
                assert(!verifyKernelABI(patcher,1,base,memory.size(),old));
            }
        }
        reset();patcher.symbols[table]=0;assert(!verifyKernelABI(patcher,1,base,memory.size(),old));
        reset();patcher.symbols[table]=base+memory.size()-16;
        assert(!verifyKernelABI(patcher,1,base,memory.size(),old));
        reset();assert(!verifyKernelABI(patcher,1,base,0,old));
        assert(!verifyKernelABI(patcher,1,~uintptr_t{0}-8,32,old));
        reset();assert(!verifyKernelABI(patcher,1,base,1024,old));
        reset();uintptr_t zero=0;memcpy(memory.data()+256+guards[0].slot,&zero,8);
        assert(verifyKernelABI(patcher,1,base,memory.size(),old)); // 不把头部槽误认为地址点。
    }
    puts("PASS: actual ABI checks; all 12 slots, named targets, full getters, address-point and bounds rejection");
}
'''.replace('FUNCTIONS', functions)
    file = WORK / 'test.cpp'
    file.write_text(fixture)
    executable = WORK / ('test-sanitize' if args.sanitize else 'test')
    flags = ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if args.sanitize else ['-O2']
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-Wall', '-Wextra', '-Wshadow', '-Werror', *flags,
                    '-I'+str(WORK/'stubs'), '-I'+str(ROOT/'NootedRed'), str(file), '-o', str(executable)],
                   check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == '__main__':
    main()
