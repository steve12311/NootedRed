"""验证扫描缓冲、字宽前缀和事务内映像索引复用，不访问 GPU。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/cache-scan-regression'


def main():
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source = r'''
#include <cassert>
#include <cstdio>
#include <vector>
#include <UserCacheResolver.hpp>
using namespace UserCacheResolver;
static constexpr uint64_t base=0x100001;
static std::vector<uint8_t> memory;
static uint64_t fail=0;
static int reader(uint64_t address,void* output,size_t size){
 if(address<base||address-base>memory.size()||size>memory.size()-(address-base))return 1;
 if(fail>=address&&fail-address<size)return 1;
 memcpy(output,memory.data()+address-base,size);return 0;
}
int main(){
 unsigned cases=0;uint32_t random=0x12345678;
 auto next=[&](){random^=random<<13;random^=random>>17;random^=random<<5;return random;};
 // 每个掩码和非掩码字节均与原逐字节谓词对照，包含非对齐读取。
 for(unsigned sample=0;sample<1024;sample++){
  uint8_t values[32],bytes[33];bool masks[32];Prefix prefix;
  for(unsigned i=0;i<32;i++){values[i]=next();masks[i]=(next()%3)==0;bytes[i+1]=values[i];prefix.set(i,values[i],masks[i]);}
  for(unsigned changed=0;changed<=32;changed++){
   if(changed<32)bytes[changed+1]^=1;
   bool expected=true;for(unsigned i=0;i<32;i++)expected&=masks[i]||bytes[i+1]==values[i];
   assert(prefix.matches(bytes+1)==expected);cases++;
   if(changed<32)bytes[changed+1]^=1;
  }
 }
 uint8_t pattern[95];for(unsigned i=0;i<sizeof(pattern);i++)pattern[i]=i+1;
 uint8_t scan[Input::ScanSize];
 for(unsigned size:{32,33,95})for(unsigned offset:{0,1,31,992,993,994,1023,1024,4064,4065,4095,4096,8097}){
  memory.assign(8192,0xcc);memcpy(memory.data()+offset,pattern,size);
  for(bool buffered:{false,true}){
   Input input{reader,{base,memory.size()}};if(buffered)input.scanBuffer=scan;
   uint64_t found=0;
   assert(scanFunction(input,input.cache,size,32,[&](const uint8_t* p){return memcmp(p,pattern,32)==0;},
       [&](uint64_t a){uint8_t bytes[95];return input.read(a,bytes,size)&&memcmp(bytes,pattern,size)==0;},found));
   assert(found==base+offset);cases++;
  }
 }
 for(bool buffered:{false,true})for(unsigned kind=0;kind<7;kind++){
  memory.assign(16384,0xcc);memcpy(memory.data()+4090,pattern,95);
  Input input{reader,{base,memory.size()}};if(buffered)input.scanBuffer=scan;
  if(kind==0)memcpy(memory.data()+12000,pattern,95); // 跨缓冲区的重复候选。
  if(kind==1)memory[4090+94]^=1; // 前缀正确，完整函数错误。
  if(kind==2)memory[4090]^=1;
  if(kind==3)fail=base+memory.size()-1;
  if(kind==4)input.reads=32768;
  if(kind==5)input.bytes=32*1024*1024;
  if(kind==6)for(unsigned i=0;i<130;i++)memcpy(memory.data()+i*96,pattern,32);
  uint64_t found=0;
  assert(!scanFunction(input,input.cache,95,32,[&](const uint8_t* p){return memcmp(p,pattern,32)==0;},
       [&](uint64_t a){uint8_t bytes[95];return input.read(a,bytes,95)&&memcmp(bytes,pattern,95)==0;},found));
  if(kind==6)assert(input.candidates==129);
  fail=0;cases++;
 }
 for(unsigned prefix:{0,33}){
  Input input{reader,{base,4096}};input.scanBuffer=scan;uint64_t found=123;
  assert(!scanFunction(input,input.cache,95,prefix,[](const uint8_t*){return true;},
      [](uint64_t){return true;},found)&&found==0&&input.reads==0);cases++;
 }
 // 同一文本仍完整扫描，4 KiB 缓冲只减少读取次数。
 unsigned reads[2]{};
 for(unsigned variant=0;variant<2;variant++){
  memory.assign(2*1024*1024,0xcc);Input input{reader,{base,memory.size()}};if(variant)input.scanBuffer=scan;
  uint64_t found=0;assert(!scanFunction(input,input.cache,95,32,[](const uint8_t*){return false;},
       [](uint64_t){return true;},found));reads[variant]=input.reads;assert(!input.failed);cases++;
 }
 assert(reads[1]*3<reads[0]);
 // 目录只在同一 Input、同一基址、已完整核验过的路径子集间复用。
 const ImageReference images[]={{"/x",3},{"/y",3}},subset[]={{"/y",3}},other[]={{"/z",3}};
 Input input{reader,{base,4096}};input.imageRefs=images;input.imageCount=2;input.tableRead=true;
 input.imageAddresses[0]=base+100;input.imageAddresses[1]=base+200;
 input.selectImages(base,subset,1);assert(input.tableRead&&input.imageRefs==images&&input.imageAddresses[1]==base+200);cases++;
 input.selectImages(base,other,1);assert(!input.tableRead&&input.imageRefs==other&&input.imageAddresses[0]==0);cases++;
 input.tableRead=true;input.selectImages(base+4096,other,1);assert(!input.tableRead);cases++;
 Input fresh{reader,{base,4096}};fresh.selectImages(base,subset,1);assert(!fresh.tableRead&&fresh.imageAddresses[0]==0);cases++;
 printf("PASS %u scan cases: exact masked-prefix equivalence, unaligned/chunk boundaries, full-function/ambiguity/failure rejection, per-transaction index; reads %u -> %u\n",cases,reads[0],reads[1]);
}
'''
    path, executable = WORK / 'test.cpp', WORK / 'test'
    path.write_text(source)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-Wall', '-Wextra', '-Werror',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    str(path), '-o', str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == '__main__':
    main()
