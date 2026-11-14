#!/usr/bin/env python3
"""Maintainer packaging of existing components; consumers do not extract libraries."""
from pathlib import Path
import argparse
import json
import plistlib
import shutil
import subprocess
import zipfile
from artifacts import copy_tree, make_variant, macho_uuid, sha

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
KERNELS = ['AMDRadeonServiceManager', 'AMDRadeonX5000', 'AMDRadeonX5000HWServices', 'AMDRadeonX6000Framebuffer']
SHARED = ['AMDShared', 'AMDRadeonX5000Shared', 'AMDRadeonX6000Shared']


def build(inputs, output, archive=False):
    profile = json.loads((ROOT / 'profile.json').read_text())
    if output.exists():
        raise ValueError('Output already exists; choose a new package directory')
    output.mkdir(parents=True)
    system = output / 'payload/system'
    data = output / 'payload/data'
    expected = {r['id']:r for r in profile['drivers']}
    for name in KERNELS:
        src = Path(inputs['kernel_bundles']) / (name + '.kext')
        for info in [src/'Contents/Info.plist', *src.glob('Contents/PlugIns/*.kext/Contents/Info.plist')]:
            p = plistlib.loads(info.read_bytes());binary = info.parent/'MacOS'/p['CFBundleExecutable']
            if p['CFBundleIdentifier'] not in expected or macho_uuid(binary).lower() != expected[p['CFBundleIdentifier']]['uuid'].lower():
                raise ValueError('Kernel donor does not match the supported software profile')
        copy_tree(src, system/'System/Library/Extensions'/src.name)
    for name in SHARED:
        src = Path(inputs['shared_bundles'])/(name+'.bundle')
        copy_tree(src, system/'System/Library/Extensions'/src.name)
    lib = system/'usr/lib/NRedMetalCompat.dylib';lib.parent.mkdir(parents=True)
    shutil.copyfile(inputs['compat_library'],lib);lib.chmod(0o755)
    copy_tree(Path(inputs['nootedred']),output/'payload/efi/Kexts/NootedRed27.kext')
    loader = data/'usr/local/libexec/nred-auxkcload';loader.parent.mkdir(parents=True)
    shutil.copyfile(inputs['aux_loader'],loader);loader.chmod(0o755)
    metadata = Path(inputs['metal_metadata'])
    variant_records = {}
    for device in profile['devices']:
        bundle = output/'variants'/device/'System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle'
        for f in metadata.rglob('*'):
            if f.is_file() and 'MacOS' not in f.parts and '_CodeSignature' not in f.parts:
                dst=bundle/f.relative_to(metadata);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(f,dst);dst.chmod(f.stat().st_mode&0o777)
        binary=bundle/'Contents/MacOS/AMDRadeonX5000MTLDriver'
        make_variant(Path(inputs['metal_unpatched']),device,REPO,binary)
        subprocess.run(['codesign','--force','--sign','-',str(bundle)],check=True)
        variant_records[device]={'files':[], 'native_validation': 'original scoped implementation passed' if device=='164C' else 'not hardware tested'}
        print('Prepared Metal variant',device,flush=True)
    # Runtime helpers are built from the source shipped with the installer.
    for name,source,frameworks in [('nred-km-launch','km_launch.c',[]),('nred-metal-probe',str(REPO/'tools/metal-compat/metal_probe.m'),['Foundation','Metal','IOKit'])]:
        target=data/'usr/local/libexec'/name
        cmd=['clang','-arch','x86_64','-Wall','-Wextra','-Werror']
        if frameworks:cmd+=['-fobjc-arc']
        for f in frameworks:cmd+=['-framework',f]
        cmd += [str(ROOT/source) if not Path(source).is_absolute() else source,'-o',str(target)]
        subprocess.run(cmd,check=True);subprocess.run(['codesign','--force','--sign','-',str(target)],check=True)
    daemon={'Label':'org.nootedred.metal-verify','RunAtLoad':True,'LaunchOnlyOnce':True,'ProcessType':'Background',
            'ProgramArguments':['/usr/local/libexec/nred-metal-probe','--run'],
            'StandardOutPath':'/var/log/nootedred-metal.log','StandardErrorPath':'/var/log/nootedred-metal.log'}
    f=data/'Library/LaunchDaemons/org.nootedred.metal-verify.plist';f.parent.mkdir(parents=True);f.write_bytes(plistlib.dumps(daemon))
    licenses=output/'licenses';licenses.mkdir();shutil.copyfile(inputs['aux_license'],licenses/'NullMoth-aux-loader.txt');shutil.copyfile(REPO/'LICENSE',licenses/'NootedRed.txt')
    (output/'COMPONENTS.md').write_text('''# Experimental AMD graphics stack\n\nContains existing Sequoia 24H32 Apple AMD kernel drivers, rebuilt Metal/compiler libraries, NootedRed donor support, a descriptor ABI adapter and the NullMoth auxiliary-collection loader. Apple components retain their original notices; the source-tool license does not relicense them. See licenses/ for the open-source components.\n\nThe original scoped 164C implementation passed desktop, render and compute tests. This portable package and other device variants require their own boot acceptance. No user identity, account, EFI config, target kernel collection or system log is included.\n''')
    manifest={'format':1,'profile':profile,'files':[],'variants':variant_records,'no_automatic_reboot':True}
    for f in sorted((output/'payload').rglob('*')):
        if f.is_file():
            rel=f.relative_to(output/'payload');scope=rel.parts[0]
            manifest['files'].append({'source':str(f.relative_to(output)),'scope':scope,'target':str(Path(*rel.parts[1:])),'sha256':sha(f),'mode':f.stat().st_mode&0o777})
    for device in variant_records:
        root=output/'variants'/device
        for f in sorted(root.rglob('*')):
            if f.is_file():variant_records[device]['files'].append({'source':str(f.relative_to(output)),'scope':'system','target':str(f.relative_to(root)),'sha256':sha(f),'mode':f.stat().st_mode&0o777})
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    # Include the standalone consumer tool; no extraction/build dependencies.
    for name in ['stack.py','artifacts.py','Install.command']:
        shutil.copyfile(ROOT/name,output/name)
        if name=='Install.command':(output/name).chmod(0o755)
    if archive:
        target=output.with_suffix('.zip')
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
            for f in sorted(output.rglob('*')):
                if f.is_file():z.write(f,f.relative_to(output))
        target.with_suffix('.zip.sha256').write_text(sha(target)+'  '+target.name+'\n')
        print(target)
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--zip',action='store_true');a=p.parse_args()
    build(json.loads(a.inputs.read_text()),a.out.resolve(),a.zip)
