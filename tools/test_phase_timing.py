"""Fase 0: cronometro per fase (spento non registra, acceso accumula media/massimo e pubblica a cadenza)."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/phase-timing'


def main():
    stubs = WORK / 'stubs'
    (stubs / 'IOKit').mkdir(parents=True, exist_ok=True)
    (stubs / 'kern').mkdir(parents=True, exist_ok=True)
    (stubs / 'IOKit/IOTypes.h').write_text('#pragma once\n#include <cstdint>\n'
                                           'using UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    (stubs / 'kern/clock.h').write_text('#pragma once\n#include <IOKit/IOTypes.h>\n'
                                        'inline UInt64 fakeClock=0;\n'
                                        'inline UInt64 mach_absolute_time(){return fakeClock;}\n'
                                        'inline void absolutetime_to_nanoseconds(UInt64 a,UInt64* ns){*ns=a;}\n')
    (stubs / 'NRed.hpp').write_text('#pragma once\n#include <map>\n#include <string>\n#include <IOKit/IOTypes.h>\n'
                                    'struct NRed{std::map<std::string,UInt32> props;unsigned writes=0;'
                                    'static NRed& singleton(){static NRed n;return n;}'
                                    'void setProp32(const char* k,UInt32 v){props[k]=v;++writes;}};\n')
    source = WORK / 'test.cpp'
    source.write_text(r'''
#include <cassert>
#include <cstdio>
#include <UserPhaseTiming.hpp>
namespace UserPhaseTiming {
UInt8& enabledFlag(){static UInt8 v{0};return v;}
Slot* slots(){static Slot v[PhaseCount]{};return v;}
UInt32& publishTicket(){static UInt32 v{0};return v;}
}
using namespace UserPhaseTiming;
#define slots slots()
static UInt32 prop(const char* key){auto& p=NRed::singleton().props;auto it=p.find(key);return it==p.end()?~0U:it->second;}
int main(){
    auto& nred=NRed::singleton();
    // Spento: nessuna registrazione e nessuna scrittura in ioreg oltre al flag.
    setEnabled(false);
    assert(prop("NRedPhaseTiming")==0);
    fakeClock=1000;{Timer t(Phase::CoordinateFull);fakeClock=9000;}
    assert(slots[0].samples==0&&nred.writes==1);
    // Acceso: 2 ms e 4 ms -> media 3000 us, massimo 4000 us.
    setEnabled(true);
    fakeClock=1000000;{Timer t(Phase::DisplayWrite);fakeClock=3000000;}
    fakeClock=10000000;{Timer t(Phase::DisplayWrite);fakeClock=14000000;}
    const auto write=unsigned(Phase::DisplayWrite);
    assert(slots[write].samples==2&&slots[write].max==4000000);
    assert(prop("NRedPhaseDisplayWriteAvgUs")==~0U);            // pubblica solo il coordinatore
    // stop() anticipato: il distruttore non registra una seconda volta.
    fakeClock=20000000;{Timer t(Phase::Header);fakeClock=20500000;t.stop();fakeClock=99000000;}
    assert(slots[unsigned(Phase::Header)].samples==1&&slots[unsigned(Phase::Header)].sum==500000);
    // retarget: la chiamata saltata va nel suo slot e fa pubblicare tutto.
    fakeClock=30000000;{Timer t(Phase::CoordinateFull);t.retarget(Phase::CoordinateSkipped);fakeClock=30200000;}
    assert(slots[unsigned(Phase::CoordinateFull)].samples==0&&slots[unsigned(Phase::CoordinateSkipped)].samples==1);
    assert(prop("NRedPhaseDisplayWriteAvgUs")==3000&&prop("NRedPhaseDisplayWriteMaxUs")==4000);
    assert(prop("NRedPhaseDisplayWriteSamples")==2&&prop("NRedPhaseHeaderAvgUs")==500);
    assert(prop("NRedPhaseCoordinateSkippedAvgUs")==200&&prop("NRedPhaseVideoAvgUs")==~0U);
    // Cadenza: primi 4 campioni del coordinatore, poi ogni 64.
    unsigned publishes=0;
    for(unsigned i=0;i<200;i++){
        const auto before=nred.writes;
        fakeClock=100000000+i*10;{Timer t(Phase::CoordinateFull);fakeClock+=5;}
        if(nred.writes!=before)++publishes;
    }
    assert(publishes==3+3);                                      // ticket 2..4 e 64,128,192
    // Orologio che torna indietro: ignorato.
    const auto samples=slots[0].samples;
    fakeClock=500;{Timer t(Phase::CoordinateFull);fakeClock=100;}
    assert(slots[0].samples==samples);
    puts("PASS phase timing: off is silent, avg/max/samples, early stop, retarget, publish cadence");
}
''')
    exe = WORK / 'test'
    subprocess.run([shutil.which('clang++') or 'clang++', '-std=c++20', '-O1', '-fsanitize=address,undefined',
                    '-I' + str(stubs), '-I' + str(ROOT / 'NootedRed'), str(source), '-o', str(exe)],
                   check=True, timeout=120)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
