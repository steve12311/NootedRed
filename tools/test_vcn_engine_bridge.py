"""编译实际引擎、SML 与跨驱动 cast 路径，验证四种 VCN2 核显的状态与槽位。"""
from pathlib import Path
import subprocess
from test_vcn_native_load import function

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vcn/engine-bridge'


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    vcn = (ROOT / 'NootedRed/VCN.cpp').read_text()
    functions = '\n'.join(function(vcn, prefix) for prefix in [
        'bool supported()', 'bool VCN::ready()', 'bool  VCN::allocated()', 'void* VCN::allocateEngine()',
        'OSMetaClassBase* safeCast(', 'void* createSML(', 'void signalWork('])
    allocation = function((ROOT / 'NootedRed/X5000.cpp').read_text(), 'bool X5000::allocateHWEngines(')
    fixture = r'''
#include <array>
#include <optional>
#include <GPUDriversAMD/Accel/HWEngine.hpp>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <map>
#include <string>
#include <VCNCapabilities.hpp>
#define SYSLOG(...) ((void)0)
using mach_vm_address_t=uintptr_t;
struct NRed {
    unsigned device=0x1638;
    std::map<std::string,unsigned> properties;
    static NRed& singleton(){static NRed n;return n;}
    unsigned getDeviceID(){return device;}
    void setProp32(const char* k,unsigned v){properties[k]=v;}
};
unsigned kernelMinor=6;
const PenguinWizardry::KernelVersion& currentKernelVersion(){static std::optional<PenguinWizardry::KernelVersion> current;return current.emplace(25,kernelMinor);}
bool requested=true,loadCompleted=true,has5000=true,has6000=true,hooks=true,engineAllocated=false;
struct OSMetaClass {
    unsigned size=64;void* object=nullptr;
    unsigned getClassSize()const{return size;}
    void* alloc(){return object;}
};
struct OSMetaClassBase {const OSMetaClass* meta;const OSMetaClass* getMetaClass(){return meta;}};
struct Pair {const OSMetaClass* oldClass;const OSMetaClass* newClass;};
Pair pairs[4]{};
OSMetaClass engine;OSMetaClass* engineClass=&engine;
mach_vm_address_t safeCastOriginal=0,sml5000=0,sml6000=0,signalOriginal=0;
void (*notifyAccess)(void*)=nullptr;
namespace VCN {bool ready();bool allocated();void* allocateEngine();}
template<typename T>T& getMember(void* object,unsigned offset){return *reinterpret_cast<T*>(static_cast<uint8_t*>(object)+offset);}
template<typename T>struct Field {unsigned offset;Field operator+(unsigned delta)const{return {offset+delta};}T& operator()(void* object){return getMember<T>(object,offset);}};
struct X5000 {
    OSMetaClass pm4,sdma;OSMetaClass *pm4EngineMC=&pm4,*sdmaEngineMC=&sdma;
    Field<void*> pm4EngineField{0x3B8},sdma0EngineField{0x3C0};Field<bool> hasVCN0Field{0xB7};
    static X5000& singleton(){static X5000 x;return x;}
    bool allocateHWEngines(void* const);
};
FUNCTIONS
ALLOCATION
OSMetaClassBase* stockCast(const OSMetaClassBase* object,const OSMetaClass* target){
    return object&&object->meta==target?const_cast<OSMetaClassBase*>(object):nullptr;
}
unsigned selected=0,configuration=0,notified=0,nativeSignals=0;
void* stockSML(unsigned value){selected=5000;configuration=value;return &selected;}
void* nativeSML(unsigned value){selected=6000;configuration=value;return &selected;}
void notify(void*){notified++;}void nativeSignal(void*){nativeSignals++;}
int main(){
    safeCastOriginal=reinterpret_cast<uintptr_t>(stockCast);
    sml5000=reinterpret_cast<uintptr_t>(stockSML);sml6000=reinterpret_cast<uintptr_t>(nativeSML);
    signalOriginal=reinterpret_cast<uintptr_t>(nativeSignal);notifyAccess=notify;
    unsigned engineObject=1,pm4Object=2,sdmaObject=3;
    auto& x=X5000::singleton();x.pm4.object=&pm4Object;x.sdma.object=&sdmaObject;
    for(unsigned minor:{0U,1U,6U,7U,99U})for(unsigned id:{0x15E7U,0x1636U,0x1638U,0x164CU}){
        kernelMinor=minor;
        NRed::singleton().device=id;requested=loadCompleted=has5000=has6000=hooks=true;
        engineAllocated=false;engine.object=&engineObject;
        alignas(8) std::array<uint8_t,0x408> object{};
        assert(x.allocateHWEngines(object.data())&&VCN::allocated());
        assert(getMember<void*>(object.data(),0x3B8)==&pm4Object);
        assert(getMember<void*>(object.data(),0x3C0)==&sdmaObject);
        assert(getMember<void*>(object.data(),0x3F8)==&engineObject);
        for(unsigned value:{0U,0x10000U,0x20000U,0x40000U,0x70000U,1U,0x80000U,0xFFFFFFFFU}){
            createSML(value);const bool generic=(value&0xFFF8FFFFU)!=0;
            assert(selected==(generic?6000U:5000U));assert(configuration==(generic?value|1U:value));
        }
        notified=nativeSignals=0;signalWork(object.data());assert(notified==1&&nativeSignals==0);
        OSMetaClass oldClass,newClass;oldClass.size=64;newClass.size=80;
        pairs[0]={&oldClass,&newClass};OSMetaClassBase oldObject{&oldClass},newObject{&newClass};
        assert(safeCast(&newObject,&oldClass)==&newObject);
        assert(safeCast(&oldObject,&newClass)==nullptr);
        assert(safeCast(nullptr,&oldClass)==nullptr);
        // 使用另一数组基址，证明 VCN 写入来自 ObjectField 与原生枚举而不是 0x3F8。
        x.pm4EngineField.offset=0x400;
        alignas(8) std::array<uint8_t,0x508> relocated{};
        assert(x.allocateHWEngines(relocated.data()));
        assert(getMember<void*>(relocated.data(),0x440)==&engineObject);
        assert(getMember<void*>(relocated.data(),0x3F8)==nullptr);
        x.pm4EngineField.offset=0x3B8;
        engine.object=nullptr;x.hasVCN0Field(object.data())=true;
        assert(x.allocateHWEngines(object.data()));assert(!VCN::allocated()&&!loadCompleted);
        assert(!x.hasVCN0Field(object.data())&&getMember<void*>(object.data(),0x3F8)==nullptr);
        assert(NRed::singleton().properties.at("NRedVCNEngineAllocated")==0);
        createSML(1);assert(selected==5000&&configuration==1);
        signalWork(object.data());assert(nativeSignals==1);
        assert(safeCast(&newObject,&oldClass)==nullptr);
    }
    for(unsigned id:{0x15D8U,0x15DDU,0x6863U,0x731FU,0xFFFF1638U}){
        NRed::singleton().device=id;loadCompleted=has5000=has6000=hooks=true;
        assert(!VCN::ready()&&VCN::allocateEngine()==nullptr);
    }
    NRed::singleton().device=0x1638;
    for(bool* state:{&requested,&loadCompleted,&has5000,&has6000,&hooks}){
        *state=false;assert(!VCN::ready());*state=true;
    }
    puts("PASS: four VCN2 engine slots, allocation failure, SML selection, cast size checks and GFX access routing");
}
'''.replace('FUNCTIONS', functions).replace('ALLOCATION', allocation)
    util = WORK / "stubs/Headers"
    util.mkdir(parents=True, exist_ok=True)
    (util / "kern_util.hpp").write_text('#pragma once\n#include <IOKit/IOTypes.h>\n')
    source = WORK / 'test.cpp'
    source.write_text(fixture)
    executable = WORK / 'test'
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-I'+str(WORK/'stubs'), '-I'+str(ROOT/'NootedRed'), str(source), '-o', str(executable)],
                   check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == '__main__':
    main()
