"""执行三系统原生 OpenGL 指令，对照 SE/CU、DualQuad、PM4 与设备隔离。"""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/opengl'


def main():
    records = json.loads((WORK / 'patches.json').read_text())
    arrays, entries = [], []
    for i, row in enumerate(records):
        hw, dual = row['hw'], row['dual']
        patched_hw = bytes.fromhex(hw['native'])[:0x128] + bytes.fromhex(hw['patched'])
        codes = [bytes.fromhex(hw['native']), patched_hw, bytes.fromhex(dual['native']), bytes.fromhex(dual['patched'])]
        for k, code in enumerate(codes):
            arrays.append(f'static const uint8_t C{i}_{k}[]={{'+','.join(map(str, code))+'};')
        entries.append('{'+','.join([str(row['major']), hex(row['deviceID']), hex(hw['address']),
                                    *map(hex, hw['constants']), hex(dual['address']), hex(dual['submit']),
                                    *[f'C{i}_{k}' for k in range(4)]])+'}')
    harness = r'''
#include <array>
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <sys/mman.h>
ARRAYS
struct Record {unsigned major,device;uint64_t hw,c0,c1,dual,submit;const uint8_t *oldHw,*newHw,*oldDual,*newDual;};
static Record records[]={RECORDS};
static unsigned calls=0;
static void put32(void* p,unsigned i,uint32_t v){memcpy(static_cast<uint8_t*>(p)+i,&v,4);}
static void put64(void* p,unsigned i,uint64_t v){memcpy(static_cast<uint8_t*>(p)+i,&v,8);}
static uint32_t get32(const void* p,unsigned i){uint32_t v;memcpy(&v,static_cast<const uint8_t*>(p)+i,4);return v;}
static void submit(void* ctx,unsigned count){calls++;assert(count==2);put32(ctx,0x1398,get32(ctx,0x1398)^0xa000);}
struct Code {
 uint8_t* p;
 Code(const uint8_t* bytes,unsigned size,uint64_t address,const Record& r,bool hw){
  p=static_cast<uint8_t*>(mmap(nullptr,4096,3,MAP_PRIVATE|MAP_ANON,-1,0));assert(p!=MAP_FAILED);memcpy(p,bytes,size);
  unsigned refs=0;
  if(hw){for(unsigned i=0;i+8<=size;i++)if(memcmp(bytes+i,"\xc5\xf8\x28\x05",4)==0){
   int32_t d;memcpy(&d,bytes+i+4,4);auto target=address+i+8+d;assert(target==r.c0||target==r.c1);
   unsigned slot=target==r.c0?1024:1040;d=int32_t(slot-i-8);memcpy(p+i+4,&d,4);refs++;
  }assert(refs==3);uint32_t constants[8]={1,17,31,63,2,9,27,45};memcpy(p+1024,constants,sizeof(constants));}
  else{for(unsigned i=0;i+5<=size;i++)if(bytes[i]==0xe8){int32_t d;memcpy(&d,bytes+i+1,4);
   assert(address+i+5+d==r.submit);d=int32_t(1024-i-5);memcpy(p+i+1,&d,4);refs++;
  }assert(refs==1);p[1024]=0x48;p[1025]=0xb8;uint64_t target=reinterpret_cast<uint64_t>(submit);
   memcpy(p+1026,&target,8);p[1034]=0xff;p[1035]=0xe0;}
  assert(mprotect(p,4096,5)==0);
 }
 ~Code(){assert(munmap(p,4096)==0);}
};
using Hw=void(*)(void*,void*,unsigned);using Dual=void(*)(void*);
int main(){
 uint32_t random=1;unsigned cases=0;
 for(auto& r:records){
  Code oh(r.oldHw,552,r.hw,r,true),nh(r.newHw,552,r.hw,r,true);
  Code od(r.oldDual,142,r.dual,r,false),nd(r.newDual,142,r.dual,r,false);
  // 穷举 PCI，证明载荷只选择本机核显，独显及其他 APU 均保留原生结果。
  std::array<uint8_t,0x204> input{};put32(input.data(),8,0);put32(input.data(),0x20,8);
  std::array<uint8_t,180> left{},right{};
  std::array<uint8_t,0x3200> ctx{};uint32_t packet[3]{};uint8_t info[180]{};
  put64(ctx.data(),0x12d8,reinterpret_cast<uint64_t>(info));put32(ctx.data(),0x30ec,4);
  put64(ctx.data(),0xd60,reinterpret_cast<uint64_t>(packet)+64);
  for(unsigned id=0;id<=0xffff;id++){
   put32(input.data(),0,id);left.fill(0);right=left;
   reinterpret_cast<Hw>(oh.p)(left.data(),input.data(),0x10000);
   reinterpret_cast<Hw>(nh.p)(right.data(),input.data(),0x10000);
   assert(get32(left.data(),0x78)==4&&get32(left.data(),0x64)==32);
   assert(get32(right.data(),0x78)==(id==r.device?1U:4U));
   assert(get32(right.data(),0x64)==(id==r.device?8U:32U));
   if(id==r.device){put32(left.data(),0x78,1);put32(left.data(),0x64,8);}assert(left==right);
   put32(info,0x0c,id);put32(ctx.data(),0x1398,0xa5a50000);put64(ctx.data(),0xd68,reinterpret_cast<uint64_t>(packet));
   reinterpret_cast<Dual>(nd.p)(ctx.data());assert((get32(ctx.data(),0x1398)&1)==(id==r.device));cases++;
  }
  // 三个家族分支、无效修订、未分类标志、原始 32 位溢出及随机控制位。
  for(unsigned revision:{0U,19U,20U,39U,40U,49U,50U,255U,0xffffffffU})
   for(unsigned flags:{0U,0x10000U,0x20000U,0x40000U,0x80000U})for(unsigned k=0;k<64;k++){
    for(auto& x:input){random=random*1664525+1013904223;x=uint8_t(random>>24);}
    for(auto& x:left){random=random*1664525+1013904223;x=uint8_t(random>>24);}right=left;
    put32(input.data(),0,k&1?r.device:0x6863);put32(input.data(),8,revision);
    reinterpret_cast<Hw>(oh.p)(left.data(),input.data(),flags);reinterpret_cast<Hw>(nh.p)(right.data(),input.data(),flags);
    if((k&1)&&(flags&0x70000)&&revision<=49){put32(left.data(),0x78,1);
     put32(left.data(),0x64,get32(input.data(),0x20)*get32(left.data(),0x80));}
    if(left!=right){for(unsigned i=0;i<left.size();i++)if(left[i]!=right[i])
     fprintf(stderr,"HW mismatch OS=%u PCI=%x rev=%u flags=%x offset=%x stock=%x fixed=%x\n",r.major,r.device,revision,flags,i,left[i],right[i]);}
    assert(left==right);cases++;
   }
  for(unsigned id:{r.device,0x6863U})for(unsigned blend=0;blend<16;blend++)for(unsigned caps=0;caps<4;caps++)
   for(unsigned full:{0U,1U}){
    for(auto& x:ctx){random=random*1664525+1013904223;x=uint8_t(random>>24);}
    put32(info,0xc,id);put32(info,0xa0,caps);put64(ctx.data(),0x12d8,reinterpret_cast<uint64_t>(info));
    put32(ctx.data(),0x30ec,blend);put64(ctx.data(),0xd68,reinterpret_cast<uint64_t>(packet));
    put64(ctx.data(),0xd60,reinterpret_cast<uint64_t>(packet)+(full?12:64));
    auto initial=ctx;uint32_t initialColor=get32(ctx.data(),0x1398);
    calls=0;reinterpret_cast<Dual>(od.p)(ctx.data());assert(calls==full);
    auto native=ctx;uint32_t oldPacket[3];memcpy(oldPacket,packet,12);
    ctx=initial;calls=0;reinterpret_cast<Dual>(nd.p)(ctx.data());assert(calls==full);
    auto expected=initialColor&~1U;
    if((blend&4)&&((caps&2)||id==r.device))expected|=1;
    if(full)expected^=0xa000;
    assert(get32(ctx.data(),0x1398)==expected&&packet[2]==expected);
    assert(packet[0]==oldPacket[0]&&packet[1]==oldPacket[1]);
    put32(native.data(),0x1398,expected);assert(ctx==native);cases++;
   }
 }
 printf("PASS %u OpenGL native cases: 3 OS, 6 APU payloads, exhaustive PCI, SE/CU, conditional DualQuad and native PM4 submit\n",cases);
}
'''.replace('ARRAYS', '\n'.join(arrays)).replace('RECORDS', ',\n'.join(entries))
    path, exe = WORK / 'native.cpp', WORK / 'native'
    path.write_text(harness)
    subprocess.run(['clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', str(path), '-o', str(exe)],
                   check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
