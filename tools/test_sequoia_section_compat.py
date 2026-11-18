"""以冻结的 15.8.1 函数验证 15.8.3 节机制；旧版节布局仅为模拟，不替代真实缓存验证。"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import subprocess

from metal_cache import array
from build_sequoia_surface import INIT, CONSTRUCTOR, SETTINGS, SUBMIT, SYMBOL
from vpf.cppdata import parse

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/sequoia-unified/regression'
NAMESPACE = 'UserSequoiaSurfaceData'


def normalized(code, refs):
    result = bytearray(code)
    for offset, *_ in refs:
        assert 0 <= offset <= len(result) - 4
        result[offset:offset + 4] = bytes(4)
    return bytes(result)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((ROOT / 'tools/fixtures/sequoia-surface-15.8.1.json').read_text())
    profile = json.loads((ROOT / 'tools/fixtures/sequoia-compat.json').read_text())
    assert baseline['uuid'] == profile['uuid'] and baseline['version'] == profile['version']
    hashes = [profile['metal'][INIT]['sha256'], profile['overrideContextSha256'],
              profile['metal'][CONSTRUCTOR]['sha256'], profile['metal'][SYMBOL]['sha256'],
              profile['metal'][SETTINGS]['sha256']]
    for fn, digest in zip(baseline['surface']['functions'], hashes):
        assert hashlib.sha256(bytes.fromhex(fn['original'])).hexdigest() == digest
    assert hashlib.sha256(bytes.fromhex(baseline['submit']['functions'][0]['original'])).hexdigest() == profile['metal'][SUBMIT]['sha256']
    current = parse(ROOT / 'NootedRed/UserSequoiaSurfaceData.hpp')
    source = ''
    deltas = defaultdict(set)
    for key, ns, label in [('surface', NAMESPACE, 'Old'), ('submit', NAMESPACE + '::Submit', 'OldSubmit')]:
        old = baseline[key]
        images = current.array(ns, 'ResolverImages').items
        assert old['images'] == [row[0] for row in images]
        targets = {(row[0], row[1]): row for row in current.array(ns, 'SectionTargets').items}
        mapped = {}
        functions = current.array(ns, 'ResolverFunctions').items
        assert len(functions) == len(old['functions'])
        source += f'namespace {label} {{\n'
        source += f'inline constexpr auto& Images = {ns}::ResolverImages;\n'
        for i, (before, after) in enumerate(zip(old['functions'], functions)):
            code = bytes.fromhex(before['original'])
            refs = current.array(ns, f'ResolverOriginalRefs{i}').items
            assert before['image'] == after[0]
            assert normalized(code, before['originalRefs']) == normalized(current.byte_array(ns, f'ResolverOriginal{i}'), refs)
            for kind in ['original', 'patched']:
                new_refs = current.array(ns, f'Resolver{kind.title()}Refs{i}').items
                old_refs = before[kind + 'Refs']
                assert [(r[0], r[1], r[3]) for r in old_refs] == [(r[0], r[1], r[3]) for r in new_refs]
                for a, b in zip(old_refs, new_refs):
                    path = old['images'][a[1]]
                    target = targets.get((path, b[2]))
                    if target:
                        deltas[(path, target[2], target[3])].add(a[2] - b[2])
                        mapped[(path, a[2])] = [path, a[2], *target[2:]]
                    else:
                        assert a[2] == b[2], '非数据节目标也发生了位移，需要真实旧缓存核验'
            source += array(f'Bytes{i}', code)
            source += f'inline constexpr UserCacheResolver::Reference Refs{i}[] = {{\n'
            source += ''.join('    {' + ','.join(str(v) + 'ULL' for v in r) + '},\n' for r in before['originalRefs'])
            source += '};\n'
        source += 'inline constexpr UserCacheResolver::Function Functions[] = {\n'
        for i, fn in enumerate(old['functions']):
            source += f'    {{{fn["image"]}, {fn["rva"]}ULL, Bytes{i}, sizeof(Bytes{i}), Refs{i}, std::size(Refs{i}), Refs{i}, std::size(Refs{i})}},\n'
        source += '};\ninline constexpr UserCacheSectionData::Target Targets[] = {\n'
        for path, rva, segment, section, offset, command, index in mapped.values():
            source += f'    {{"{path}", {rva}ULL, "{segment}", "{section}", {offset}ULL, {command}, {index}}},\n'
        source += '};\n}\n'
    assert all(len(values) == 1 for values in deltas.values()), '同一节内的引用相对布局不一致'
    for device, patches in baseline['devices'].items():
        for i, (f, offset) in enumerate(baseline['locations']):
            old_refs = [[r[0] - offset, *r[1:]] for r in baseline['surface']['functions'][f]['patchedRefs']
                        if offset <= r[0] and r[0] + 4 <= offset + len(bytes.fromhex(patches[i]))]
            new_refs = [[r[0] - offset, *r[1:]] for r in current.array(NAMESPACE, f'ResolverPatchedRefs{f}').items
                        if offset <= r[0] and r[0] + 4 <= offset + len(bytes.fromhex(patches[i]))]
            name = ['Init' + device, 'Patched1', 'Patched2', 'Scratch' + device][i]
            assert normalized(bytes.fromhex(patches[i]), old_refs) == normalized(current.byte_array(NAMESPACE, name), new_refs)
    harness = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <UserSurfaceSyncResolver.hpp>
static std::map<std::string,unsigned> properties;
#include "user-cache-dynamic.hpp"
SOURCE
static void setup(bool old){
 if(old)cache(Old::Images,Old::Functions,true,Old::Targets,std::size(Old::Targets));
 else {using namespace UserSequoiaSurfaceData;cache(ResolverImages,ResolverFunctions,true,SectionTargets,std::size(SectionTargets));}
 const auto& fn=old?Old::Functions[0]:UserSequoiaSurfaceData::ResolverFunctions[0];
 const auto constant=imageAddresses[0]+0x71064;word32(constant,0x40400000);
 rel(functionAddresses[0],fn.originalRefs[0].offset,constant);
 const auto dump=destination(fn.originalRefs[2],old?Old::Functions:UserSequoiaSurfaceData::ResolverFunctions,5);
 put(dump,UserSurfaceSyncData::NativeDump,sizeof(UserSurfaceSyncData::NativeDump));
}
static bool resolve(unsigned device,bool buffered){
 UserSurfaceSyncResolver::Plan plan;plan.input={reader,{}};if(buffered)plan.input.scanBuffer=plan.scan;
 const bool ok=UserSurfaceSyncResolver::resolveMetal(plan.input,base,device,plan,24);
 assert(writes==0&&protects==0);if(!ok)return false;
 for(unsigned i=0;i<4;i++){uint8_t bytes[512];auto&p=plan.patches[i];assert(reader(base+p.offset,bytes,p.size)==0);assert(memcmp(bytes,p.original,p.size)==0||memcmp(bytes,p.patched,p.size)==0);put(base+p.offset,p.patched,p.size);}
 return true;
}
int main(){unsigned cases=0;
 for(bool old:{false,true})for(bool buffered:{false,true})for(auto&device:UserSequoiaSurfaceData::DevicePatches){
  setup(old);assert(resolve(device.deviceID,buffered));assert(resolve(device.deviceID,buffered));cases+=2;
  setup(old);const auto&fn=old?Old::Functions[2]:UserSequoiaSurfaceData::ResolverFunctions[2];
  uint8_t bad=fn.original[32]^1;put(functionAddresses[2]+32,&bad,1);assert(!resolve(device.deviceID,buffered));cases++;
  setup(old);auto ref=(old?Old::Functions[2]:UserSequoiaSurfaceData::ResolverFunctions[2]).originalRefs[0];
  uint8_t field[4];uint64_t target=0;assert(reader(functionAddresses[2]+ref.offset,field,4)==0);
  assert(UserSharedCache::relativeTarget(functionAddresses[2]+ref.offset,0,field,target));
  rel(functionAddresses[2],ref.offset,target+4);assert(!resolve(device.deviceID,buffered));cases++;
 }
 for(bool old:{false,true})for(bool buffered:{false,true}){
  if(old)cache(OldSubmit::Images,OldSubmit::Functions,true,OldSubmit::Targets,std::size(OldSubmit::Targets));
  else {using namespace UserSequoiaSurfaceData::Submit;cache(ResolverImages,ResolverFunctions,true,SectionTargets,std::size(SectionTargets));}
  UserSharedCache::Input input{reader,{}};uint8_t scan[UserSharedCache::Input::ScanSize];if(buffered)input.scanBuffer=scan;
  uint64_t offset=0;assert(UserSurfaceSyncResolver::resolveSubmitForOS(input,base,offset,24));assert(base+offset==functionAddresses[0]);cases++;
 }
 printf("PASS %u cases: 15.8.1 frozen functions / 15.8.3 functions, simulated section layouts, six GPUs, idempotency and target/instruction rejection\n",cases);
}
'''.replace('SOURCE', source)
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    cpp, exe = WORK / 'test.cpp', WORK / 'test'
    cpp.write_text(harness)
    subprocess.run(['clang++', '-std=c++23', '-O2', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    '-I' + str(ROOT / 'tools/fixtures'), str(cpp), '-o', str(exe)], check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)
    print(f'PASS static comparison: six full functions, six device payloads, {len(deltas)} section translations; old Mach-O section headers unavailable')


if __name__ == '__main__':
    main()
