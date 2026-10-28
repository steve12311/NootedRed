"""验证解析诊断计数器的默认关闭、事件累加与并发记录。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/resolution-diagnostics'


def main():
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source = r'''
#include <cassert>
#include <cstdio>
#include <thread>
#include <vector>
#include <VPF/ResolutionDiagnostics.hpp>
using namespace VPF;
static void record(unsigned threads, unsigned rounds){
 std::vector<std::thread> workers;
 for(unsigned t=0;t<threads;t++)workers.emplace_back([=]{
  for(unsigned i=0;i<rounds;i++){
   ResolutionDiagnostics::counters.record(ResolutionEvent::Cold);
   ResolutionDiagnostics::counters.record(ResolutionEvent::Hot);
   ResolutionDiagnostics::counters.record(ResolutionEvent::Hit,2);
   ResolutionDiagnostics::counters.record(ResolutionEvent::ScanBytes,4096);
   if((i&15)==0)ResolutionDiagnostics::counters.record(ResolutionEvent::Count);
  }
 });
 for(auto& worker:workers)worker.join();
}
int main(){
 ResolutionDiagnostics::setEnabled(false);assert(!ResolutionDiagnostics::enabled());
 ResolutionDiagnostics::setEnabled(true);assert(ResolutionDiagnostics::enabled());
 ResolutionDiagnostics::counters={};
 record(4,10000);
 assert(ResolutionDiagnostics::counters.load(ResolutionEvent::Cold)==40000);
 assert(ResolutionDiagnostics::counters.load(ResolutionEvent::Hot)==40000);
 assert(ResolutionDiagnostics::counters.load(ResolutionEvent::Hit)==80000);
 assert(ResolutionDiagnostics::counters.load(ResolutionEvent::ScanBytes)==40000ULL*4096);
 assert(ResolutionDiagnostics::counters.load(ResolutionEvent::Miss)==0);
 assert(ResolutionDiagnostics::counters.load(ResolutionEvent::Count)==0);
 // 记录量允许清零阶段与新事件相互独立。
 ResolutionDiagnostics::counters={};
 assert(ResolutionDiagnostics::counters.load(ResolutionEvent::Cold)==0);
 ResolutionDiagnostics::counters.record(ResolutionEvent::Fallback,7);
 assert(ResolutionDiagnostics::counters.load(ResolutionEvent::Fallback)==7);
 printf("PASS diagnostics: default-off flag, concurrent atomic counters, byte accounting, ignored sentinel\n");
}
'''
    path, executable = WORK / 'test.cpp', WORK / 'test'
    path.write_text(source)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-Wall', '-Wextra', '-Werror', '-pthread',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    str(path), '-o', str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == '__main__':
    main()
