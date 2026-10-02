"""完整执行原生 RB+ 分支与状态写入块，验证双源条件和所有控制位。"""
from pathlib import Path
import hashlib
import subprocess
from metal_cache import CacheReader

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/legacy-blend-v1"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    cache = CacheReader()
    try:
        code = cache.read(0x7ffb10c776f1, 80)
        assert hashlib.sha256(code).hexdigest() == "eb14736b5315f6ae6123f9d167660b7b089719205d3b19c0a2dd839f69bc594a"
        assert code[23:25].hex() == "7428"
    finally:
        cache.close()
    harness = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <sys/mman.h>
CODE
extern "C" void invoke(void*,void*,void*,unsigned,void*);
asm(".text\n.globl _invoke\n_invoke:\npushq %rbp\nmovq %rsp,%rbp\npushq %rbx\npushq %r12\nsubq $512,%rsp\n"
    "movq %rdi,%r12\nmovq %rsi,-456(%rbp)\nmovq %rdx,-400(%rbp)\nmovq $0,-392(%rbp)\nmovl %ecx,-384(%rbp)\n"
    "callq *%r8\naddq $512,%rsp\npopq %r12\npopq %rbx\npopq %rbp\nretq\n");
int main(int argc,char**){
 auto* p=static_cast<uint8_t*>(mmap(nullptr,4096,3,MAP_PRIVATE|MAP_ANON,-1,0));assert(p!=MAP_FAILED);
 memcpy(p,code,80);p[80]=0xc3;memcpy(p+128,p,81);p[128+23]=p[128+24]=0x90;assert(mprotect(p,4096,5)==0);
 if(argc>1){std::array<uint8_t,512> regs{};std::array<uint8_t,180> info{};std::array<uint8_t,64> device{};
  invoke(regs.data(),info.data(),device.data(),1,p);assert((regs[0x28]&1)!=0);return 0;}
 unsigned cases=0;uint32_t random=1;
 for(bool hardware:{false,true})for(bool dual:{false,true})for(bool enabled:{false,true})for(unsigned i=0;i<1024;i++){
  std::array<uint8_t,512> left,right;std::array<uint8_t,180> info{};std::array<uint8_t,64> device{};
  for(auto& b:left){random=random*1664525+1013904223;b=uint8_t(random>>24);}right=left;
  info[0xa0]=hardware?2:0;uint64_t settings=uint64_t(enabled)<<34;memcpy(device.data()+8,&settings,8);
  auto infoBefore=info;auto deviceBefore=device;auto leftExpected=left,rightExpected=right;
  uint32_t value;memcpy(&value,left.data()+0x28,4);
  uint32_t oldValue=(value&~1U)|unsigned(hardware&&(dual||!enabled));
  uint32_t newValue=(value&~1U)|unsigned(dual||!enabled);
  memcpy(leftExpected.data()+0x28,&oldValue,4);memcpy(rightExpected.data()+0x28,&newValue,4);
  invoke(left.data(),info.data(),device.data(),dual,p);invoke(right.data(),info.data(),device.data(),dual,p+128);
  assert(left==leftExpected&&right==rightExpected&&info==infoBefore&&device==deviceBefore);cases++;
 }
 assert(munmap(p,4096)==0);
 printf("PASS: %u native RB+ gate/store cases; dual source or disabled RB+ sets DISABLE_DUAL_QUAD independently of missing hardware flag; other bits preserved\n",cases);
}
'''.replace("CODE", "static const uint8_t code[]={" + ",".join(map(str, code)) + "};")
    source = WORK / "test-gate.cpp"
    source.write_text(harness)
    exe = WORK / "test-gate"
    subprocess.run(["clang++", "-std=c++23", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(exe)],
                   check=True, timeout=60)
    before = subprocess.run([str(exe), "--original-must-fail"], capture_output=True, timeout=60)
    assert before.returncode != 0 and b"regs[0x28]" in before.stderr
    result = subprocess.run([str(exe)], check=True, capture_output=True, text=True, timeout=60)
    (WORK / "gate-results.txt").write_text(result.stdout)
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
