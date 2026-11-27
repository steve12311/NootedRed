"""执行真实 VCN 调用点安装器，覆盖三个版本的写前核验、重定向和失败回滚。"""
import argparse
from pathlib import Path
import subprocess
from test_vcn_native_load import function

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vcn/kernel-install'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;\n')
    source = (ROOT / 'NootedRed/VCN.cpp').read_text()
    code = 'template<typename T>\n' + function(source, 'bool resolve(') + '\n' + function(source, 'bool install(')
    harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <VCNKernelProfiles.hpp>
using mach_vm_address_t=uintptr_t;using SInt32=int32_t;
static unsigned major=25,transitions=0;static bool writable=false,denyWrite=false;
struct Version {unsigned major()const{return ::major;}};static Version currentKernelVersion(){return {};}
#define SYSLOG(...) ((void)0)
constexpr int KERN_SUCCESS=0;
template<typename T,size_t N>constexpr size_t arrsize(const T(&)[N]){return N;}
struct KernelPatcher{
 static constexpr int kernelWriteLock=0;bool routeOK=true;
 std::map<std::string,uintptr_t> symbols;
 struct RouteRequest{template<typename T>RouteRequest(const char*,T,uintptr_t&) {}};
 uintptr_t solveSymbol(size_t,const char* n,uintptr_t,size_t,bool){return symbols[n];}
 void clearError(){}bool routeMultiple(size_t,RouteRequest*,size_t,uintptr_t,size_t){assert(!writable);return routeOK;}
};
struct MachInfo{static int setKernelWriting(bool value,int){
 if(value&&denyWrite)return 42;assert(writable!=value);writable=value;transitions++;return 0;
}};
static void signalWork(){}static void clientStart(){}static void clientStop(){}
static void surfaceCopy(){}
static uintptr_t signalOriginal=0,clientStart6000=0,clientStop6000=0,copy6000=0;
CODE
int main(){
 for(unsigned host:{23U,24U,25U}){
  major=host;size_t count=0;const auto* patches=VCNKernelProfiles::patches(host,count);assert(patches&&count&&count<=32);
  std::vector<uint8_t> memory(128*1024);const uintptr_t base=reinterpret_cast<uintptr_t>(memory.data());
  KernelPatcher kernel;
  const auto reset=[&]{
   memory.assign(memory.size(),0);kernel.symbols.clear();kernel.routeOK=true;denyWrite=false;writable=false;transitions=0;
   size_t functionOffset=4096;
   for(size_t i=0;i<count;i++){
    const auto& p=patches[i];auto& a=kernel.symbols[p.symbol];if(!a){a=base+functionOffset;functionOffset+=4096;}
    assert(p.offset+6<=4096);memcpy(reinterpret_cast<void*>(a+p.offset),p.original,6);
   }
   kernel.symbols["__ZN26AMDRadeonX6000_AMDHardware22signalGPUWorkSubmittedEv"]=base+100*1024;
  };
  reset();const auto original=memory;assert(install(kernel,1,base,memory.size()));assert(transitions==2&&!writable);
  unsigned waitDisable=0,waitEnable=0;
  for(size_t i=0;i<count;i++){
   const auto& p=patches[i];const auto address=kernel.symbols.at(p.symbol)+p.offset;const auto* b=reinterpret_cast<const uint8_t*>(address);
   if(p.newSlot){uint32_t slot=0;memcpy(&slot,b+2,4);assert(b[0]==0xFF&&b[1]==0x90&&slot==p.newSlot);}
   else{int32_t displacement=0;memcpy(&displacement,b+1,4);assert(b[0]==0xE8&&b[5]==0x90);
    assert(address+5+displacement==kernel.symbols.at("__ZN26AMDRadeonX6000_AMDHardware22signalGPUWorkSubmittedEv"));}
   if(std::string(p.symbol).find("11waitForIdleEj")!=std::string::npos){waitDisable+=p.newSlot==0x3E8;waitEnable+=p.newSlot==0x3F0;}
  }
  assert(waitDisable==2&&waitEnable==2);
  reset();kernel.routeOK=false;assert(!install(kernel,1,base,memory.size()));assert(memory==original&&transitions==4&&!writable);
  reset();denyWrite=true;assert(!install(kernel,1,base,memory.size()));assert(memory==original&&transitions==0);
  for(size_t i=0;i<count;i++){
   reset();memory[kernel.symbols.at(patches[i].symbol)+patches[i].offset-base]^=1;auto bad=memory;
   assert(!install(kernel,1,base,memory.size()));assert(memory==bad&&transitions==0);
  }
  reset();kernel.symbols[patches[0].symbol]=base+memory.size();assert(!install(kernel,1,base,memory.size())&&transitions==0);
  reset();assert(!install(kernel,1,UINTPTR_MAX-4,16)&&transitions==0);
 }
 puts("PASS actual kernel install: Darwin 23/24/25 tables, GFXOFF pairs, rel32 target, every call-site rejection, route failure rollback");
}
'''.replace('CODE', code)
    cpp, exe = WORK / 'test.cpp', WORK / 'test'
    cpp.write_text(harness)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if args.sanitize else []
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', *flags, '-I'+str(WORK / 'stubs'),
                    '-I'+str(ROOT / 'NootedRed'), str(cpp), '-o', str(exe)], check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
