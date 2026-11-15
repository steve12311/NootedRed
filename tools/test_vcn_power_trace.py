"""Fase 5, passo A: la traccia del power gating VCN passa argomenti e risultato invariati e pubblica solo i cambi."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vcn-power-trace'


def main():
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;\n')
    source = WORK / 'test.cpp'
    source.write_text(r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <VCNPowerTrace.hpp>
using namespace VCNPowerTrace;
alignas(16) static UInt8 hwmgr[0x2000];
alignas(16) static UInt8 renoirData[0x400];
static UInt32 calls=0,lastGate=99,lastInstance=99,nextResult=ResultOK;
static UInt32 renoirGate(void* h,UInt32 gate,UInt32 instance){
    assert(h==hwmgr);++calls;lastGate=gate;lastInstance=instance;
    *reinterpret_cast<UInt32*>(renoirData+RenoirGatedOffset)=gate?1:0;return nextResult;}
static UInt32 otherGate(void*,UInt32,UInt32){return ResultOK;}
static std::map<std::string,UInt32> props;static unsigned writes=0;
static void out(const char* k,UInt32 v){props[k]=v;++writes;}
static void setBackend(void* fn){std::memcpy(hwmgr+BackendOffset,&fn,sizeof(fn));}
int main(){
    UInt8* data=renoirData;std::memcpy(hwmgr+BackendDataOffset,&data,sizeof(data));
    State s{};s.original=renoirGate;s.renoirBackend=reinterpret_cast<void*>(renoirGate);s.layoutVerified=true;
    // Nessuna chiamata: niente da pubblicare.
    publish(s,out);assert(writes==0);
    // Spegnimento: argomenti e risultato passano invariati, stato letto dopo la chiamata.
    setBackend(reinterpret_cast<void*>(renoirGate));
    assert(call(s,hwmgr,1,0)==ResultOK&&calls==1&&lastGate==1&&lastInstance==0);
    publish(s,out);
    assert(props["NRedVCNPGGateCalls"]==1&&props["NRedVCNPGUngateCalls"]==0&&props["NRedVCNPGBackend"]==2);
    assert(props["NRedVCNPGGated"]==1&&props["NRedVCNPGLastGate"]==1&&props["NRedVCNPGLastResult"]==ResultOK);
    // Nessun cambio: nessuna scrittura.
    const auto before=writes;publish(s,out);assert(writes==before);
    // Accensione con errore: risultato restituito tale e quale e contato.
    nextResult=2;assert(call(s,hwmgr,0,3)==2&&lastInstance==3);nextResult=ResultOK;
    publish(s,out);
    assert(props["NRedVCNPGUngateCalls"]==1&&props["NRedVCNPGFailures"]==1&&props["NRedVCNPGGated"]==0);
    // Backend sconosciuto: lo stato Renoir non viene letto.
    setBackend(reinterpret_cast<void*>(otherGate));call(s,hwmgr,1,0);publish(s,out);
    assert(props["NRedVCNPGBackend"]==4&&props["NRedVCNPGGated"]==Unknown);
    // Nessun backend: PHM risponde OK senza fare nulla.
    setBackend(nullptr);call(s,hwmgr,1,0);publish(s,out);
    assert(props["NRedVCNPGBackend"]==1&&props["NRedVCNPGGateCalls"]==3);
    // 未验证布局时只计数，不能读取硬编码的对象偏移。
    State unknown{};unknown.original=otherGate;
    assert(call(unknown,reinterpret_cast<void*>(1),0,0)==ResultOK);
    assert(unknown.backend==0&&unknown.gated==Unknown&&unknown.ungateCalls==1);
    puts("PASS VCN power trace: passthrough, counters, backend check, unknown-layout safety, publish only on change");
}
''')
    exe = WORK / 'test'
    subprocess.run([shutil.which('clang++') or 'clang++', '-std=c++20', '-O1', '-fsanitize=address,undefined',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'), str(source), '-o', str(exe)],
                   check=True, timeout=120)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
