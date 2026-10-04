"""执行新旧原生初始化代码，检查目标延迟同步位、配置覆盖和其他字节保持。"""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/user-surface-sync"


def verify(entry, records_key, shared_srd):
    arrays = []
    for r in entry[records_key]:
        for prefix, field in [("old", "original"), ("new", "patched")]:
            arrays.append(f"static const uint8_t {prefix}_{r['label']}[]={{" +
                          ",".join(map(str, bytes.fromhex(r[field]))) + "};")
    harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <array>
#include <sys/mman.h>
ARRAYS
static unsigned calls=0,dumps=0;
static uint64_t overrideMask=0,overrideValue=0;
static uint64_t overrideSettings(void* p){
 calls++;uint64_t flags;memcpy(&flags,p,8);flags=(flags&~overrideMask)|overrideValue;memcpy(p,&flags,8);
 asm volatile("xor %%esi,%%esi":::"rsi");return 0xdeadbeef;
}
static void dump(void*){dumps++;}
struct Executable{
 uint8_t* p;
 Executable(const uint8_t* code,size_t size,uint64_t base){
  p=static_cast<uint8_t*>(mmap(nullptr,4096,3,MAP_PRIVATE|MAP_ANON,-1,0));assert(p!=MAP_FAILED);
  memcpy(p,code,size);unsigned linked=0;
  for(size_t i=0;i+5<=size;i++)if(code[i]==0xe8 || code[i]==0xe9){
   int32_t v;memcpy(&v,code+i+1,4);uint64_t target=base+i+5+v;
   if(target==0x7ffb10c5422bULL || target==0x7ffb10c545c6ULL){
    const unsigned t=target==0x7ffb10c5422bULL?1024:1040;
    p[t]=0x48;p[t+1]=0xb8;uintptr_t f=t==1024?reinterpret_cast<uintptr_t>(overrideSettings):reinterpret_cast<uintptr_t>(dump);
    memcpy(p+t+2,&f,8);p[t+10]=0xff;p[t+11]=0xe0;v=int32_t(t-i-5);memcpy(p+i+1,&v,4);linked++;
   }
  }
  assert(linked==(size==10?1U:2U));
  if(size==215){unsigned constants=0;
   for(size_t i=0;i+9<=size;i++)if(memcmp(code+i,"\xc4\xe2\x79\x18\x05",5)==0){
    int32_t v=int32_t(1100-i-9);memcpy(p+i+5,&v,4);float value=3.0f;memcpy(p+1100,&value,4);constants++;
   }assert(constants==1);
  }
  assert(mprotect(p,4096,5)==0);
 }
 ~Executable(){assert(munmap(p,4096)==0);}
};
using Init=void(*)(void*,void*);
static void put32(void* p,unsigned o,uint32_t v){memcpy(static_cast<uint8_t*>(p)+o,&v,4);}
int main(int argc,const char*[]){
 Executable a(old_init,sizeof(old_init),0x7ffb10d71a28ULL),b(new_init,sizeof(new_init),0x7ffb10d71a28ULL);
 // 新覆盖入口跳到同一页内部的 stub，保持真实相对位置。
 uint8_t* bridge=static_cast<uint8_t*>(mmap(nullptr,4096,3,MAP_PRIVATE|MAP_ANON,-1,0));assert(bridge!=MAP_FAILED);
 memcpy(bridge,b.p,4096);memcpy(bridge+215,new_override,10);
 assert(mprotect(bridge,4096,5)==0);
 Executable c(old_override,sizeof(old_override),0x7ffb10d71affULL);
 // 参数可用于先确认旧函数确实不能满足目标修复条件。
 if(argc>1){uint8_t settings[128]{},info[180]{};put32(info,12,0x1638);
  reinterpret_cast<Init>(a.p)(settings,info);assert((settings[3]&1)==0);return 0;}
 uint32_t random=1;unsigned cases=0;
 for(uint32_t id:{0x1638U,0x15e7U,0x1636U,0x164cU,0x15d8U,0x15ddU,0x6860U,0xffff15e7U,0xffff1638U})for(unsigned i=0;i<2048;i++){
  alignas(16) std::array<uint8_t,128> left,right;std::array<uint8_t,180> info;
  for(auto& x:left){random=random*1664525+1013904223;x=uint8_t(random>>24);}right=left;
  for(auto& x:info){random=random*1664525+1013904223;x=uint8_t(random>>24);}
  put32(info.data(),12,id);put32(info.data(),0x78,i%5);
  overrideMask=i&1?0xffffffffffffffffULL:0;
  overrideValue=i&1?(0x123456789abcde80ULL^(uint64_t(random)<<32)):0;
  calls=dumps=0;reinterpret_cast<Init>(a.p)(left.data(),info.data());assert(calls==1&&dumps==1);
  calls=dumps=0;reinterpret_cast<Init>(b.p)(right.data(),info.data());assert(calls==1&&dumps==1);
  if(id==TARGET_DEVICE){left[3]&=0xfe;SHARED_MASK}assert(left==right);
  // 单独调用原生覆盖入口：包括配置显式开启 DCC 和 RSI 被调用者破坏的情况。
  calls=dumps=0;reinterpret_cast<Init>(c.p)(left.data(),info.data());assert(calls==1&&dumps==0);
  calls=dumps=0;reinterpret_cast<Init>(bridge+215)(right.data(),info.data());assert(calls==1&&dumps==0);
  if(id==TARGET_DEVICE){left[3]&=0xfe;SHARED_MASK}assert(left==right);cases++;
 }
 // 穷举完整 PCI ID 空间，保证同进程中的其他 GPU 保留原生配置。
 for(uint32_t id=0;id<=0xffff;id++){
  uint8_t settings[128]{},info[180]{};put32(info,12,id);settings[3]=1;settings[8]=0x40;
  overrideMask=overrideValue=0;reinterpret_cast<Init>(bridge+215)(settings,info);
  assert((settings[3]&1)==(id==TARGET_DEVICE?0:1));
  SHARED_ASSERT
 }
 assert(munmap(bridge,4096)==0);printf("PASS: %u native init/override cases; only selected target sync/SRD placement bits change\n",cases);
}
'''.replace("ARRAYS", "\n".join(arrays)).replace("SHARED_MASK", "left[8]&=0xbf;" if shared_srd else "")
    harness = harness.replace("TARGET_DEVICE", hex(entry['device_id'])).replace(
        "SHARED_ASSERT", "assert((settings[8]&0x40)==(id==TARGET_DEVICE?0:0x40));".replace(
            "TARGET_DEVICE", hex(entry['device_id'])) if shared_srd else "assert(settings[8]==0x40);")
    path = WORK / "test-native.cpp"
    path.write_text(harness)
    exe = WORK / "test-native"
    subprocess.run(["clang++", "-std=c++23", "-O2", "-Wall", "-Wextra", "-Werror",
                    str(path), "-o", str(exe)], check=True, timeout=60)
    # 旧代码失败是预期；单独子进程验证，不降低新代码断言。
    result = subprocess.run([str(exe), "--original-must-fail"], capture_output=True, timeout=60)
    if result.returncode == 0 or b"settings[3]" not in result.stderr:
        raise AssertionError("旧实现未复现 延迟同步开启条件")
    print("CONFIRMED: original initializer leaves deferred surface synchronization enabled")
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == "__main__":
    data = json.loads((WORK / "patch-data.json").read_text())
    for entry in data['device_records']:
        print(f"VERIFY PCI {entry['device_id']:#06x}", flush=True)
        verify(entry, "records", False)
        verify(entry, "srd_shared_records", True)
