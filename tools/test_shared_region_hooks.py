"""Compile the production entry wrappers: chaining, native errors, trace bounds, stack budget."""
from pathlib import Path
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/shared-region-hooks'


def wrapper(module):
    source = (ROOT / 'NootedRed' / f'{module}.cpp').read_text(encoding='utf-8')
    marker = '// Only the first eight calls are traced'
    start = source.index(marker)
    end = source.index('// namespace', start)
    body = source[start:end].rstrip()
    # Drop the anonymous namespace's closing brace, not the wrapper's closing brace.
    body = body[:body.rfind('}')]
    assert '__attribute__((noinline))' in body
    assert body.index('reinterpret_cast<SharedRegionCheck>') < body.index('patchSharedRegion(process')
    helper = source[source.index('int patchSharedRegion('):start]
    assert 'reinterpret_cast<SharedRegionCheck>(originalCheck)' not in helper
    return body


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    common = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>
using UInt32=uint32_t;using proc_t=void*;
struct SharedRegionCheckArgs {uint64_t startAddress;};
using SharedRegionCheck=int(*)(proc_t,SharedRegionCheckArgs*,int*);
enum class Stage {Called,OriginalError};
static void stage(Stage){}
static int proc_pid(proc_t p){return int(reinterpret_cast<uintptr_t>(p));}
static std::vector<int> order;
static int nativeResult=0,nativeDepth=0;
static unsigned displayLogs=0,videoLogs=0;
static void recordTrace(const char* module){(std::string(module)=="UserSurfaceSync"?displayLogs:videoLogs)++;}
#define SYSLOG(module,...) recordTrace(module)
__attribute__((noinline))
static int native(proc_t,SharedRegionCheckArgs*,int* out){
 assert(nativeDepth==0);nativeDepth++;order.push_back(0);*out=123;nativeDepth--;return nativeResult;
}
'''
    source = common
    for namespace, module, number in [('Display', 'UserSurfaceSync', 1), ('Video', 'UserVideoDecode', 2)]:
        source += f'''
namespace {namespace} {{
static uintptr_t originalCheck=0;
__attribute__((noinline))
static int patchSharedRegion(proc_t,SharedRegionCheckArgs*,int result){{
 assert(nativeDepth==0);order.push_back({number});return result;
}}
{wrapper(module)}
}}
'''
    source += r'''
int main(){
 SharedRegionCheckArgs args{0x1000};
 for(bool reversed:{false,true}){
  Display::hookTraceCount=Video::hookTraceCount=0;displayLogs=videoLogs=0;
  Display::originalCheck=reinterpret_cast<uintptr_t>(reversed?Video::wrappedSharedRegionCheck:native);
  Video::originalCheck=reinterpret_cast<uintptr_t>(reversed?native:Display::wrappedSharedRegionCheck);
  auto outer=reversed?Display::wrappedSharedRegionCheck:Video::wrappedSharedRegionCheck;
  nativeResult=0;
  for(int i=0;i<20;i++){
   order.clear();int value=0;assert(outer(reinterpret_cast<void*>(1),&args,&value)==0&&value==123);
   assert((order==std::vector<int>{0,reversed?2:1,reversed?1:2}));
  }
  assert(Display::hookTraceCount==8&&Video::hookTraceCount==8);
  assert(displayLogs==32&&videoLogs==32);
  nativeResult=37;order.clear();int value=0;
  assert(outer(nullptr,&args,&value)==37&&value==123);
  assert((order==std::vector<int>{0}));
 }
 puts("PASS both production wrapper orders; native result preserved; patch work after native return; bounded tracing");
}
'''
    path = WORK / 'test-hooks.cpp'
    path.write_text(source, encoding='utf-8')
    exe = WORK / ('test-hooks.exe' if os.name == 'nt' else 'test-hooks')
    compiler = os.environ.get('CXX') or shutil.which('clang++')
    assert compiler, 'clang++ required'
    subprocess.run([compiler, '-std=c++20', '-O2', '-Wframe-larger-than=512', '-Werror',
                    str(path), '-o', str(exe)], check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
