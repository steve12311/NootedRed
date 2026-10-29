"""从完整原生函数生成私有守卫页及每版本独立的动态匹配数据。"""
from pathlib import Path
import argparse
import hashlib
import json
import re
from audit_video_driver import symbols
from cache_resolver_data import ResolverData
from metal_cache import CacheReader,CACHE,array
import build_video_decode_patch as builder
from video_capability_patch import build_payload

ROOT=Path(__file__).resolve().parents[1]


def payload_source(payload,entries,bindings):
    out=array('Payload',payload)
    out+='inline constexpr UserVideoGuardData::Entry Entries[] = {\n'+''.join(f'    {{{f}, {size}, {offset}}},\n' for f,size,offset in entries)+'};\n'
    out+='inline constexpr UserVideoGuardData::Binding Bindings[] = {\n'+''.join(f'    {{{off}, {f}, {value}, {str(reference).lower()}}},\n' for off,f,value,reference in bindings)+'};\n'
    return out


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--major',type=int,choices=[24,25],required=True)
    parser.add_argument('--cache',type=Path,default=CACHE)
    parser.add_argument('--check',action='store_true',help='核验生成结果，不修改生成头文件')
    parser.add_argument('--payload-only',action='store_true',help='从已核实原生函数快照更新守卫，不更改缓存匹配数据')
    args=parser.parse_args(argv)
    profiles=json.loads((ROOT/'tools/fixtures/vcn-capability-compat.json').read_text())['profiles']
    profile=next(p for p in profiles if p['major']==args.major)
    work=ROOT/'build/vcn-guards'/str(args.major);work.mkdir(parents=True,exist_ok=True);builder.WORK=work
    if args.payload_only:
        target=ROOT/f'NootedRed/UserVideoGuard{args.major}Data.hpp'
        existing=target.read_text()
        payload,entries,bindings,names,offsets=build_payload(profile,work,builder.compile_payload)
        for index,name in enumerate(names):
            data=profile['functions'][name]
            if hashlib.sha256(bytes.fromhex(data['bytes'])).hexdigest()!=data['sha256']:
                raise ValueError('原生函数快照哈希不符')
            expected=bytes.fromhex(data.get('context',data)['bytes'])
            for label,value in [(f'ResolverOriginal{index}',expected),(f'Identity{index}',expected[:64])]:
                match=re.search(r'inline constexpr UInt8 '+label+r'\[\] = \{(.*?)\};',existing,re.S)
                if not match or bytes(int(v,16) for v in re.findall(r'0x([0-9A-Fa-f]{2})',match[1]))!=value:
                    raise ValueError(f'已有原生匹配数据与快照不符：{label}')
        prefix,tail=existing.split('inline constexpr UInt8 Payload[] = {',1)
        if not re.fullmatch(r'.*?inline constexpr UserVideoGuardData::Entry Entries\[\] = \{.*?\};\ninline constexpr UserVideoGuardData::Binding Bindings\[\] = \{.*?\};\n}\n',tail,re.S):
            raise ValueError('生成文件尾部结构变化')
        out=prefix+payload_source(payload,entries,bindings)+'}\n'
        if args.check:
            if existing!=out:raise ValueError('生成守卫不同步')
        else:target.write_text(out)
        print(f'Generated Darwin {args.major}: {len(entries)} guarded entries, {len(payload)} payload bytes; matching data unchanged')
        return
    cache=CacheReader(args.cache,profile['cacheUUID'])
    try:
        lines=(args.cache/'dyld_shared_cache_x86_64h.map').read_text().splitlines()
        base=int(lines[lines.index(profile['image'])+1].split()[1],16);native_names,_=symbols(cache.read,base)
        payload,entries,bindings,names,offsets=build_payload(profile,work,builder.compile_payload)
        resolver=ResolverData(cache,work)
        patches=[]
        for index,name in enumerate(names):
            data=profile['functions'][name];address=native_names[name]
            end=min(v for v in native_names.values() if v>address)
            native=cache.read(address,end-address)
            if len(native)!=data['size'] or hashlib.sha256(native).hexdigest()!=data['sha256']:
                raise ValueError(f'原生守卫函数未核实：{name}')
            if 'context' in data:
                context=data['context']
                if end!=native_names[context['following']]:raise ValueError('守卫上下文边界变化')
                native=cache.read(address,context['size'])
                if hashlib.sha256(native).hexdigest()!=context['sha256']:raise ValueError('守卫上下文变化')
            assert resolver.add(address,native,native)==index
            patches.append((address-cache.base,native[:64]))
        # 动态绑定只能使用已核验的原生 rel32 指令；不能把任意偏移解释为地址。
        for _,function,value,reference in bindings:
            if reference:
                refs=resolver.functions[function][3]
                if not any(offset==value and kind==0 for offset,_,_,kind in refs):
                    raise ValueError('守卫绑定不属于已核验的直接引用')
        out='// 由 tools/build_video_capability_patch.py 生成，不手工编辑。\n#pragma once\n#include <UserVideoGuardData.hpp>\n'
        out+=f'namespace UserVideoGuard{args.major}Data {{\n'
        out+='inline constexpr UInt32 Features = 0;\n'
        for i,(_,prefix) in enumerate(patches):out+=array(f'Identity{i}',prefix)
        out+='inline constexpr UserVideoDecodeData::Patch IdentityPatches[] = {\n'
        out+=''.join(f'    {{0x{rva:X}ULL, Identity{i}, Identity{i}, sizeof(Identity{i})}},\n' for i,(rva,_) in enumerate(patches))+'};\n'
        out+=resolver.header([(i,0) for i in range(len(names))])+resolver.section_header()
        out+=payload_source(payload,entries,bindings)
        out+='}\n'
        target=ROOT/f'NootedRed/UserVideoGuard{args.major}Data.hpp'
        if args.check:
            if target.read_text()!=out:raise ValueError(f'生成文件不同步：{target}')
        else:target.write_text(out)
        (work/'manifest.json').write_text(json.dumps({'major':args.major,'uuid':profile['cacheUUID'],'features':0,
            'payloadSize':len(payload),'entries':entries,'bindings':bindings,'offsets':offsets},indent=2)+'\n')
        print(f'Generated Darwin {args.major}: {len(entries)} guarded entries, {len(payload)} payload bytes, features=0')
    finally:cache.close()


if __name__=='__main__':main()
