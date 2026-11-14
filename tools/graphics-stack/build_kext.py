"""Build isolated NootedRed with CLT, without modifying the host EFI."""
from pathlib import Path
import concurrent.futures, hashlib, json, plistlib, re, subprocess

import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out',type=Path,required=True)
parser.add_argument('--sdk',type=Path)
parser.add_argument('--lilu',type=Path)
args=parser.parse_args()
SRC=Path(__file__).resolve().parents[2]
OUT=args.out.resolve()
OBJ=OUT/'obj'
OBJ.mkdir(parents=True,exist_ok=True)
sdk=(args.sdk or SRC/'MacKernelSDK').resolve()
lilu=((args.lilu or SRC/'Lilu')/'Lilu').resolve()
flags=['-target','x86_64-apple-macos10.15','-mkernel','-DKERNEL','-DKERNEL_PRIVATE','-D__KERNEL__',
       '-DPRODUCT_NAME=NootedRed','-DMODULE_VERSION=0.9.3','-DDEBUG=1','-DAPPLE_KEXT_ASSERTIONS=1',
       '-DMACH_ASSERT=1','-fno-builtin','-fno-stack-protector','-fno-common','-fno-asynchronous-unwind-tables',
       '-mno-red-zone','-mmmx','-msse','-msse2','-msse3','-mssse3','-mfpmath=sse','-O2',
       '-nostdinc','-I'+str(sdk/'Headers'),'-I'+str(lilu),'-I'+str(SRC/'NootedRed'),
       '-ffile-prefix-map='+str(SRC)+'=NootedRedSource','-std=c++23','-fapple-kext','-fno-exceptions','-fno-rtti','-fno-use-cxa-atexit']
pbx=(SRC/'NootedRed.xcodeproj/project.pbxproj').read_text()
phase=pbx.split('/* Begin PBXSourcesBuildPhase section */')[1].split('/* End PBXSourcesBuildPhase section */')[0]
names=re.findall(r'/\* (\S+\.cpp) in Sources \*/',phase)
sources=[]
for name in names:
    paths=list((SRC/'NootedRed').rglob(name)) if name!='plugin_start.cpp' else [lilu/'Library'/name]
    assert len(paths)==1,(name,paths)
    sources.append(paths[0])

def compile_one(p):
    o=OBJ/(p.stem+'.o')
    text=p.read_text()
    # Apple CLT 17 does not implement C23 #embed; materialize exact bytes in
    # generated translation units, retaining a #line for source diagnostics.
    def embed(m):
        data=(p.parent/m.group(1)).read_bytes()
        suffix=m.group(2).strip()
        assert suffix in ('',"suffix(, '\\0')"),suffix
        # All embedded arrays in this pinned source are const char[].
        return ','.join(str(b if b < 128 else b-256) for b in data)+( ",0" if suffix else '')
    text=re.sub(r'^#embed "([^"]+)"(.*)$',embed,text,flags=re.M)
    generated=OBJ/(p.name)
    generated.write_text('#line 1 "'+str(p)+'"\n'+text)
    command=['clang++',*flags,'-I'+str(p.parent),'-c',str(generated),'-o',str(o)]
    r=subprocess.run(command,capture_output=True,text=True)
    (OBJ/(p.stem+'.log')).write_text(r.stdout+r.stderr)
    if r.returncode: raise RuntimeError(p.name+'\n'+r.stderr[:9000])
    return o

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    objects=list(pool.map(compile_one,sources))
module=OBJ/'module.cpp'
module.write_text('''#include <mach/kmod.h>
extern "C" {
extern kern_return_t _start(kmod_info_t *, void *);
extern kern_return_t _stop(kmod_info_t *, void *);
extern kern_return_t NootedRed_kern_start(kmod_info_t *, void *);
extern kern_return_t NootedRed_kern_stop(kmod_info_t *, void *);
KMOD_EXPLICIT_DECL(org.ChefKiss.NootedRed, "0.9.3", _start, _stop)
__private_extern__ kmod_start_func_t *_realmain = NootedRed_kern_start;
__private_extern__ kmod_stop_func_t *_antimain = NootedRed_kern_stop;
__private_extern__ int _kext_apple_cc = 6000;
}
''')
subprocess.run(['clang++',*flags,'-c',str(module),'-o',str(OBJ/'module.o')],check=True)
bundle=OUT/'NootedRed27.kext'
exe=bundle/'Contents/MacOS/NootedRed'
exe.parent.mkdir(parents=True,exist_ok=True)
subprocess.run(['ld','-arch','x86_64','-kext','-static','-undefined','dynamic_lookup',
                '-platform_version','macos','10.15','15.5','-o',str(exe),
                *map(str,objects),str(OBJ/'module.o'),str(sdk/'Library/x86_64/libkmod.a')],check=True)
info=(SRC/'NootedRed/Info.plist').read_text()
for a,b in {'$(EXECUTABLE_NAME)':'NootedRed','$(PRODUCT_NAME)':'NootedRed',
            '$(PRODUCT_NAME:rfc1034identifier)':'NootedRed','$(MODULE_VERSION)':'0.9.3',
            '$(PRODUCT_BUNDLE_IDENTIFIER)':'org.ChefKiss.NootedRed'}.items(): info=info.replace(a,b)
plist=plistlib.loads(info.encode());plist['NRedDonorProfile']='Darwin 27.2 / Sequoia 24H32; opt-in'
(bundle/'Contents/Info.plist').write_bytes(plistlib.dumps(plist))
subprocess.run(['codesign','--force','--sign','-',str(bundle)],check=True)
receipt={'source_commit':subprocess.check_output(['git','-C',str(SRC),'rev-parse','HEAD'],text=True).strip(),'source_count':len(sources),
         'binary_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'bundle':str(bundle),
         'runtime_tested':False}
(OUT/'receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
