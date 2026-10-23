"""执行公共页面恢复实现，检查最大权限失败、重试及回滚前重新获取写权限。"""
from pathlib import Path
from user_patch_memory_harness import with_memory_helpers
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/user-patch-memory'


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    source = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <map>
#include <set>
using UInt32=uint32_t;using UInt64=uint64_t;
using vm_map_t=void*;using mach_vm_address_t=uint64_t;using mach_vm_size_t=uint64_t;
using natural_t=unsigned;using mach_msg_type_number_t=unsigned;
using vm_prot_t=int;using kern_return_t=int;using vm_region_recurse_info_t=int*;
struct vm_region_submap_short_info_data_64_t {int protection=0,is_submap=0;};
constexpr unsigned PAGE_SIZE=4096,VM_REGION_SUBMAP_SHORT_INFO_COUNT_64=12;
constexpr int FALSE=0,TRUE=1,KERN_SUCCESS=0;
enum {VM_PROT_NONE=0,VM_PROT_READ=1,VM_PROT_WRITE=2,VM_PROT_EXECUTE=4,VM_PROT_COPY=16};
static std::map<uint64_t,int> current,maximum;
static std::set<unsigned> failures;
static unsigned calls=0,errors=0;
static void report(int error){assert(error==42);errors++;}
static int vm_protect(vm_map_t,uint64_t address,unsigned size,int setMax,int flags){
 assert(size==PAGE_SIZE);calls++;
 if(failures.contains(calls))return 42;
 if(setMax){maximum.at(address)=flags&7;current.at(address)&=flags&7;}
 else{current.at(address)=flags&7;maximum.at(address)|=flags&7;}
 return 0;
}
BODY
static void reset(){current={{4096,3},{8192,3},{12288,5}};maximum={{4096,7},{8192,7},{12288,5}};
 failures.clear();calls=errors=0;}
int main(){
 using namespace UserPatchMemory;
 // 当前权限已恢复为 RX 时，最大权限失败仍须返回失败，并允许独立重试。
 reset();Page page{4096,5,true};failures={2};
 assert(!restorePages(nullptr,&page,1,report));
 assert(current.at(4096)==5&&maximum.at(4096)==7&&!page.writable&&errors==1);
 assert(restorePages(nullptr,&page,1,report));
 assert(current.at(4096)==5&&maximum.at(4096)==5);
 auto before=calls;assert(restorePages(nullptr,&page,1,report)&&calls==before);
 // 回滚必须真正重新获取写权限，不能把“最大权限待恢复”当成“仍然可写”。
 reset();page=Page{4096,5,true};failures={2};
 assert(!restorePages(nullptr,&page,1,report));
 assert(prepareRollback(nullptr,&page,1,4096,15,report)&&current.at(4096)==3&&page.writable);
 assert(restorePages(nullptr,&page,1,report));
 assert(current.at(4096)==5&&maximum.at(4096)==5&&!page.writable);
 // 当前权限连续失败后保留可写状态；其余页仍应恢复。
 reset();Page pages[]={{4096,5,true},{8192,5,true},{12288,5,false}};failures={1,2};
 assert(!restorePages(nullptr,pages,3,report));
 assert(pages[0].writable&&!pages[1].writable&&current.at(8192)==5&&maximum.at(8192)==5);
 assert(restorePages(nullptr,pages,3,report));
 for(auto [address,value]:current)assert(value==5&&maximum.at(address)==5);
 // 最大权限反复失败不能被后续调用跳过；也不能阻止处理其他页。
 reset();pages[0]=Page{4096,5,true};pages[1]=Page{8192,5,true};failures={2,5};
 assert(!restorePages(nullptr,pages,3,report));
 assert(current.at(4096)==5&&maximum.at(4096)==7&&maximum.at(8192)==5);
 assert(!restorePages(nullptr,pages,3,report));
 assert(restorePages(nullptr,pages,3,report));
 for(auto [address,value]:current)assert(value==5&&maximum.at(address)==5);
 puts("PASS 页面权限恢复：最大权限失败返回值、独立重试、回滚写权限及多页错误隔离");
}
'''.replace('BODY', with_memory_helpers('using UserPatchMemory::Page;'))
    path, exe = WORK / 'test-memory.cpp', WORK / 'test-memory'
    path.write_text(source)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror',
                    str(path), '-o', str(exe)], check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
