"""Fase V1 VP9: la sonda pubblica le tre chiavi solo se idonea, tutto o niente, e i nomi combaciano con il sistema."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vp9-probe'
VPX_PLIST = Path('/System/Library/Video/Plug-Ins/AppleGVAVPXDecoder.bundle/Contents/Info.plist')
GVA = Path('/System/Library/PrivateFrameworks/AppleGVA.framework/Versions/A/AppleGVA')


def check_system_names():
    """Le chiavi devono essere quelle che il plugin e AppleGVA cercano davvero su questo sistema."""
    plist = plistlib.loads(VPX_PLIST.read_bytes())
    required = {c['CMDependencies']['IORegistryRequiredKey'] for c in plist['CMClassImplementations']
                if c['CMMatchingInfo']['VTCodecType'] in ('vp09', 'qvp9')}
    assert required == {'IOGVAXDecode'}, required
    strings = subprocess.run(['dyld_info', '-section', '__TEXT', '__cstring', str(GVA)],
                             capture_output=True, text=True, check=True).stdout
    for key in ('"IOGVAVP9Decode"', '"IOGVAVP9DecodeCapabilities"', '"VTSupportedProfileArray"'):
        assert key in strings, key


def main():
    if '--offline' not in sys.argv:
        check_system_names()
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\n#include <cstddef>\nusing UInt32=uint32_t;\n')
    source = WORK / 'test.cpp'
    source.write_text(r'''
#include <cassert>
#include <cstdio>
#include <map>
#include <string>
#include <vector>
#include <VP9Probe.hpp>
using namespace VP9Probe;
struct FakeSink {
    std::map<std::string,std::string> strings;
    std::map<std::string,std::vector<UInt32>> dicts;
    std::string failOn;
    bool string(const char* k,const char* v){if(failOn==k)return false;strings[k]=v;return true;}
    bool profiles(const char* k,const char* a,const UInt32* v,size_t n){
        if(failOn==k)return false;assert(std::string(a)=="VTSupportedProfileArray");
        dicts[k]=std::vector<UInt32>(v,v+n);return true;}
    void remove(const char* k){strings.erase(k);dicts.erase(k);}
    bool empty() const {return strings.empty()&&dicts.empty();}
};
int main(){
    // Non idonea (ponte VCN non pronto): nessuna scrittura.
    {FakeSink s;assert(publish(s,false)==NotEligible&&s.empty());}
    // 就绪时发布 Profile 0 和 2。
    {FakeSink s;assert(publish(s,true)==Published);
     assert(s.strings["IOGVAXDecode"]=="2"&&s.strings["IOGVAVP9Decode"]=="1");
     assert((s.dicts["IOGVAVP9DecodeCapabilities"]==std::vector<UInt32>{0,2}));}
    {FakeSink s;assert(publish(s,true)==Published);
     assert(publish(s,false)==NotEligible&&s.empty());}
    // Ogni errore toglie le chiavi già scritte.
    for(const char* k:{"IOGVAXDecode","IOGVAVP9Decode","IOGVAVP9DecodeCapabilities"}){
        FakeSink s;s.failOn=k;assert(publish(s,true)==Failed&&s.empty());}
    puts("PASS VP9 probe: eligibility gate, three keys, profiles 0 and 2, all-or-nothing rollback");
}
''')
    exe = WORK / 'test'
    subprocess.run([shutil.which('clang++') or 'clang++', '-std=c++20', '-O1', '-fsanitize=address,undefined',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'), str(source), '-o', str(exe)],
                   check=True, timeout=120)
    subprocess.run([str(exe)], check=True, timeout=60)
    if '--offline' not in sys.argv:
        print('PASS VP9 probe: key names match AppleGVAVPXDecoder and AppleGVA on this system')


if __name__ == '__main__':
    main()
