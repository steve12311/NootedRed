#!/usr/bin/env python3
"""Install a prepared AMD graphics component package. Never reboots or uploads logs."""
from pathlib import Path, PurePosixPath
import argparse
import base64
import copy
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from artifacts import commands, sections, sha, macho_uuid

HERE=Path(__file__).resolve().parent


def safe(root, relative):
    rel=PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or any(x in ('..','.') for x in rel.parts):
        raise ValueError('Unsafe package path: '+relative)
    root=Path(root).resolve();path=root.joinpath(*rel.parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError('Path escapes selected root: '+relative)
    cursor=path
    while cursor!=root:
        if cursor.is_symlink():raise ValueError('Symlink destination is not supported: '+str(cursor))
        cursor=cursor.parent
    return path


def disk_info(path):
    candidate=Path(path)
    if candidate.exists():
        candidate=candidate.resolve()
        while not os.path.ismount(candidate) and candidate!=candidate.parent:candidate=candidate.parent
        path=candidate
    return plistlib.loads(subprocess.check_output(['diskutil','info','-plist',str(path)]))


def identities(roots):
    result={}
    for key in ['system','data','efi']:
        info=disk_info(roots[key]);result[key]=info['VolumeUUID']
    if len(set(result.values()))!=3:raise ValueError('System, Data and EFI must be distinct volumes')
    return result


def verify_roots(roots, expected):
    if Path(roots['system']).resolve()==Path('/') or Path(roots['data']).resolve()==Path('/System/Volumes/Data'):
        raise ValueError('Use an offline test system; the running system is not an install target')
    if identities(roots)!=expected:raise ValueError('Selected volume identities changed')
    group=Path(roots['preboot'])
    if not group.is_dir() or not re.fullmatch(r'[0-9a-fA-F-]{36}',group.name):
        raise ValueError('Select the target volume-group directory inside Preboot')
    # Verify the actual System/Data APFS group association, never infer it from disk numbers.
    listing=plistlib.loads(subprocess.check_output(['diskutil','apfs','listVolumeGroups','-plist']))
    matched=False
    def visit(value):
        nonlocal matched
        if isinstance(value,dict):
            text=json.dumps(value)
            groupid=value.get('APFSVolumeGroupUUID') or value.get('VolumeGroupUUID')
            if groupid and str(groupid).upper()==group.name.upper():
                if expected['system'].upper() in text.upper() and expected['data'].upper() in text.upper():matched=True
            for x in value.values():visit(x)
        elif isinstance(value,list):
            for x in value:visit(x)
    visit(listing)
    if not matched:raise ValueError('Preboot directory does not match the selected System/Data volume group')


def discover_group(system):
    identity=disk_info(system)['VolumeUUID']
    listing=plistlib.loads(subprocess.check_output(['diskutil','apfs','listVolumeGroups','-plist']))
    for container in listing.get('Containers',[]):
        for group in container.get('VolumeGroups',[]):
            volumes=group.get('Volumes',[])
            if not any(v.get('DiskUUID')==identity and v.get('Role')=='System' for v in volumes):continue
            data=next((v for v in volumes if v.get('Role')=='Data'),None)
            if data is None:raise ValueError('Target has no paired Data volume')
            data_info=disk_info(data['DeviceIdentifier'])
            if not data_info.get('MountPoint'):raise ValueError('Mount the paired Data volume first: '+data['DeviceIdentifier'])
            group_id=group['APFSVolumeGroupUUID']
            candidates=[Path('/System/Volumes/Preboot')/group_id]
            candidates += [p/group_id for p in Path('/Volumes').glob('*') if p.is_dir()]
            preboot=next((p for p in candidates if (p/'boot/System/Library/KernelCollections/BootKernelExtensions.kc').exists()),None)
            if preboot is None:raise ValueError('Mount the target Preboot volume and provide --preboot with its volume-group folder')
            return Path(data_info['MountPoint']),preboot
    raise ValueError('Could not identify the selected System/Data volume group')


def device_id(requested=None):
    if requested:return requested.lower().removeprefix('0x').upper().zfill(4)
    tree=plistlib.loads(subprocess.check_output(['ioreg','-a','-r','-c','IOPCIDevice']))
    ids=set()
    def visit(x):
        if isinstance(x,dict):
            vendor=x.get('vendor-id');device=x.get('device-id');kind=x.get('class-code')
            if isinstance(vendor,bytes) and isinstance(device,bytes) and isinstance(kind,bytes):
                if int.from_bytes(vendor,'little')==0x1002 and (int.from_bytes(kind,'little')>>16)==3:
                    ids.add(f"{int.from_bytes(device,'little')&0xffff:04X}")
            for v in x.values():visit(v)
        elif isinstance(x,list):
            for v in x:visit(v)
    visit(tree)
    if len(ids)!=1:raise ValueError('Specify --device-id for the target GPU (none or multiple AMD GPUs detected)')
    return ids.pop()


def load_package(package, device):
    manifest=json.loads((package/'manifest.json').read_text())
    if manifest.get('format')!=1 or device not in manifest['variants']:raise ValueError('Unsupported package/device profile')
    rows=manifest['files']+manifest['variants'][device]['files']
    seen=set()
    for row in rows:
        if row['scope'] not in ['system','data','efi']:raise ValueError('Invalid package scope')
        key=(row['scope'],row['target'])
        if key in seen:raise ValueError('Duplicate install path')
        seen.add(key)
        path=safe(package,row['source'])
        if not path.is_file() or sha(path)!=row['sha256']:raise ValueError('Package hash mismatch: '+row['source'])
    return manifest,rows


def inspect(roots,manifest):
    system=Path(roots['system']);data=Path(roots['data']);efi=Path(roots['efi'])
    info=plistlib.loads((system/'System/Library/CoreServices/SystemVersion.plist').read_bytes())
    if info['ProductBuildVersion'] not in manifest['profile']['target_builds']:
        raise ValueError('This package supports builds '+', '.join(manifest['profile']['target_builds']))
    required=[system/'System/Library/KernelCollections/BootKernelExtensions.kc',system/'System/Library/KernelCollections/SystemKernelExtensions.kc',
              system/'System/Library/LaunchDaemons/com.apple.kernelmanagerd.plist',system/'System/Library/Extensions/IONDRVSupport.kext/Contents/Info.plist',
              data/'Library/NullMoth/bringup/ws-gate.sh',efi/'config.plist']
    for p in required:
        if not p.is_file():raise ValueError('Missing bring-up prerequisite: '+str(p))
    gate=(data/'Library/NullMoth/bringup/ws-gate.sh').read_text()
    if 'graphics-path' not in gate or 'await_apple_accel' not in gate:
        raise ValueError('The existing WindowServer gate has no Apple accelerator path; update the base bring-up kit first')
    config=plistlib.loads((efi/'config.plist').read_bytes())
    def version(value,default):
        if not value:return default
        return tuple(int(n) for n in value.split('.'))
    lilu=[x for x in config['Kernel']['Add'] if x.get('Enabled') and 'lilu' in x['BundlePath'].lower()
          and version(x.get('MinKernel',''),(0,0,0))<=(27,2,0)<=version(x.get('MaxKernel',''),(99,99,99))]
    if not lilu:raise ValueError('The base EFI needs a Darwin-27-capable Lilu entry; a normal unsupported Lilu is not supplied by this graphics package')
    return info,config


def patch_fallback(boot,info):
    """Metadata-only fallback suppression, preserving KC layout, UUID and code."""
    binary=boot.read_bytes();entries=list(commands(binary))
    if any(cmd==0x1d for _,cmd,_ in entries):raise ValueError('Signed outer BootKC is not supported by this experimental metadata patch')
    name,_,size,offset=next(x for x in sections(binary) if x[0]=='__info')
    raw=binary[offset:offset+size];xml=raw.rstrip(b'\0');meta=plistlib.loads(xml)
    bundle=next(x for x in meta['_PrelinkInfoDictionary'] if x.get('CFBundleIdentifier')=='com.apple.iokit.IONDRVSupport')
    original=info.read_bytes()
    if hashlib.sha256(original).digest()!=bundle['_InfoPlistDigest']:raise ValueError('IONDRVSupport Info.plist differs from the boot collection')
    old=b'<string>IOPCIDevice</string>';new=b'<string>NRedNoGPU__</string>'
    keys=[k for k,v in bundle['IOKitPersonalities'].items() if v.get('IOProviderClass')=='IOPCIDevice']
    if not keys:return binary,original
    if len(keys)!=2 or original.count(old)!=2:raise ValueError('Unrecognized IONDRVSupport personality layout')
    modified=original.replace(old,new)
    start=xml.index(b'<key>CFBundleExecutable</key>\n\t\t\t<string>IONDRVSupport</string>');end=xml.index(b'\n\t\t</dict>',start)
    part=xml[start:end]
    if part.count(old)!=2:raise ValueError('Unrecognized BootKC metadata formatting')
    oldhash=base64.b64encode(bundle['_InfoPlistDigest']);newhash=base64.b64encode(hashlib.sha256(modified).digest())
    if part.count(oldhash)!=1:raise ValueError('Ambiguous plist digest')
    part=part.replace(old,new).replace(oldhash,newhash);changed=xml[:start]+part+xml[end:]
    expected=copy.deepcopy(meta);b=next(x for x in expected['_PrelinkInfoDictionary'] if x.get('CFBundleIdentifier')=='com.apple.iokit.IONDRVSupport')
    for k in keys:b['IOKitPersonalities'][k]['IOProviderClass']='NRedNoGPU__'
    b['_InfoPlistDigest']=hashlib.sha256(modified).digest()
    if len(changed)!=len(xml) or plistlib.loads(changed)!=expected:raise ValueError('Unexpected collection change')
    return binary[:offset]+changed+raw[len(xml):]+binary[offset+size:],modified


def config_with_donor(config):
    result=copy.deepcopy(config)
    for item in result['Kernel']['Add']:
        if item['BundlePath']=='NootedRed.kext' and (not item.get('MaxKernel') or item['MaxKernel'].startswith('27')):
            item['MaxKernel']='26.99.99'
    entry={'Arch':'x86_64','BundlePath':'NootedRed27.kext','Comment':'Experimental Sequoia AMD donor profile for Darwin 27.2',
           'Enabled':True,'ExecutablePath':'Contents/MacOS/NootedRed','MinKernel':'27.2.0','MaxKernel':'27.2.99','PlistPath':'Contents/Info.plist'}
    existing=[x for x in result['Kernel']['Add'] if x['BundlePath']=='NootedRed27.kext']
    if len(existing)>1:raise ValueError('Duplicate NootedRed27 entry')
    if existing:existing[0].update(entry)
    else:result['Kernel']['Add'].append(entry)
    if result.get('PlatformInfo')!=config.get('PlatformInfo') or result.get('NVRAM')!=config.get('NVRAM'):
        raise ValueError('Identity or global boot arguments unexpectedly changed')
    return result


def graphics_boot_plist(value):
    value=copy.deepcopy(value)
    flags=[x for x in value.get('Kernel Flags','').split() if x!='-NRedNoAccel' and not x.startswith('AMDBacklight=')]
    for flag in ['-NRedDonor24','-NRedBootController','-NRedBootAccel','-lilubetaall','AMDBacklight=0']:
        if flag not in flags:flags.append(flag)
    value['Kernel Flags']=' '.join(flags)
    return value


def build_plan(package,roots,device,work):
    manifest,rows=load_package(package,device);info,config=inspect(roots,manifest)
    volume_ids=identities(roots);verify_roots(roots,volume_ids)
    work.mkdir(parents=True,exist_ok=False);generated=work/'generated';generated.mkdir()
    root=work/'link-root';ext=root/'System/Library/Extensions';ext.mkdir(parents=True)
    # Link against the recipient's collections, not the publisher's collection UUIDs.
    for name in ['AMDRadeonServiceManager','AMDRadeonX5000','AMDRadeonX5000HWServices','AMDRadeonX6000Framebuffer']:
        shutil.copytree(package/'payload/system/System/Library/Extensions'/(name+'.kext'),ext/(name+'.kext'))
    for p in root.rglob('*'):os.chown(p,0,0)
    core=root/'System/Library/CoreServices';core.mkdir(parents=True)
    (core/'SystemVersion.plist').write_bytes(plistlib.dumps(info))
    system=Path(roots['system']);preboot=Path(roots['preboot']);boot=system/'System/Library/KernelCollections/BootKernelExtensions.kc';syskc=system/'System/Library/KernelCollections/SystemKernelExtensions.kc'
    preboot_system=preboot/'boot/System/Library/KernelCollections/SystemKernelExtensions.kc'
    if not preboot_system.is_file() or sha(preboot_system)!=sha(syskc):raise ValueError('SystemKC differs between System and Preboot; resolve the base boot environment first')
    dependencies=[{'path':str(p),'sha256':sha(p)} for p in [syskc,preboot_system,system/'System/Library/CoreServices/SystemVersion.plist']]
    newboot,newinfo=patch_fallback(boot,system/'System/Library/Extensions/IONDRVSupport.kext/Contents/Info.plist')
    (generated/'BootKernelExtensions.kc').write_bytes(newboot);(generated/'IONDRVSupport.plist').write_bytes(newinfo)
    auxiliary=generated/'AuxiliaryKernelExtensions.kc'
    kcroot=root/'System/Library/KernelCollections';kcroot.mkdir(parents=True)
    shutil.copyfile(generated/'BootKernelExtensions.kc',kcroot/'BootKernelExtensions.kc')
    shutil.copyfile(syskc,kcroot/'SystemKernelExtensions.kc')
    # kmutil resolves input collection/repository paths relative to -R.
    # Output paths are rooted under -R; repository paths are host-absolute.
    mapped_output=root/'AuxiliaryKernelExtensions.kc'
    cmd=['kmutil','create','-n','aux','-a','x86_64','--no-authentication','--no-authorization','--allow-missing-kdk','--no-variant-extension','-R',str(root),'-r',str(ext),
         '-B','/System/Library/KernelCollections/BootKernelExtensions.kc','-S','/System/Library/KernelCollections/SystemKernelExtensions.kc','-A','/AuxiliaryKernelExtensions.kc','--build',info['ProductBuildVersion'],'--explicit-only']
    for bid in ['com.apple.kext.AMDRadeonServiceManager','com.apple.kext.AMDRadeonX5000','com.apple.kext.AMDRadeonX5000HWServices','com.apple.kext.AMDRadeonX5000HWLibs','com.apple.kext.AMDRadeonX6000Framebuffer']:cmd+=['-b',bid]
    with (work/'kmutil.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    if not auxiliary.exists() and mapped_output.is_file():shutil.move(mapped_output,auxiliary)
    # Reject a silently empty auxiliary collection (e.g. bundle ownership problems).
    auxbytes=auxiliary.read_bytes();sec=next(x for x in sections(auxbytes) if x[0]=='__info');metadata=plistlib.loads(auxbytes[sec[3]:sec[3]+sec[2]].rstrip(b'\0'))
    linked={x.get('CFBundleIdentifier') for x in metadata['_PrelinkInfoDictionary']}
    wanted={'com.apple.kext.'+n for n in ['AMDRadeonServiceManager','AMDRadeonX5000','AMDRadeonX5000HWServices','AMDRadeonX5000HWLibs','AMDRadeonX6000Framebuffer']}
    if not wanted.issubset(linked):raise ValueError('AuxKC did not include all AMD modules; inspect kmutil.log')
    if str(uuid.UUID(bytes=metadata['_BootKCID']))!=macho_uuid(boot) or str(uuid.UUID(bytes=metadata['_PageableKCID']))!=macho_uuid(syskc):raise ValueError('AuxKC references different base collections')
    planned=[]
    def add(source,scope,target,mode=0o644):
        destination=safe(Path(roots[scope]),target)
        planned.append({'source':str(source.resolve()),'scope':scope,'relative':target,'target':str(destination),'after':sha(source),'before':sha(destination) if destination.is_file() else None,'mode':mode,'mode_before':destination.stat().st_mode&0o777 if destination.is_file() else None})
    for row in rows:add(safe(package,row['source']),row['scope'],row['target'],row['mode'])
    add(auxiliary,'data','Library/KernelCollections/AuxiliaryKernelExtensions.kc')
    for scope,path in [('system','System/Library/KernelCollections/BootKernelExtensions.kc'),('preboot','boot/System/Library/KernelCollections/BootKernelExtensions.kc')]:
        if not safe(Path(roots[scope]),path).exists():raise ValueError('Missing expected BootKC copy')
        add(generated/'BootKernelExtensions.kc',scope,path)
    add(generated/'IONDRVSupport.plist','system','System/Library/Extensions/IONDRVSupport.kext/Contents/Info.plist')
    p=generated/'config.plist';p.write_bytes(plistlib.dumps(config_with_donor(config)));add(p,'efi','config.plist')
    daemon=plistlib.loads((system/'System/Library/LaunchDaemons/com.apple.kernelmanagerd.plist').read_bytes())
    daemon.pop('Program',None);daemon['ProgramArguments']=['/usr/local/libexec/nred-km-launch']
    p=generated/'kernelmanagerd.plist';p.write_bytes(plistlib.dumps(daemon));add(p,'system','System/Library/LaunchDaemons/com.apple.kernelmanagerd.plist')
    p=generated/'graphics-path';p.write_text('apple\n');add(p,'data','Library/NullMoth/bringup/graphics-path')
    boot_paths=[('system','Library/Preferences/SystemConfiguration/com.apple.Boot.plist'),('preboot','boot/System/Library/CoreServices/com.apple.Boot.plist'),('preboot','Library/Preferences/SystemConfiguration/com.apple.Boot.plist'),('preboot','System/Library/CoreServices/com.apple.Boot.plist')]
    for i,(scope,path) in enumerate(boot_paths):
        original=safe(Path(roots[scope]),path)
        if not original.exists():continue
        value=graphics_boot_plist(plistlib.loads(original.read_bytes()));p=generated/f'boot-{i}.plist';p.write_bytes(plistlib.dumps(value));add(p,scope,path)
    if not any(row['relative'].endswith('com.apple.Boot.plist') for row in planned):raise ValueError('No volume-specific boot argument plist found')
    plan={'format':1,'roots':roots,'volume_uuids':volume_ids,'device':device,'profile':manifest['profile']['name'],'boot_uuid':macho_uuid(boot),'system_uuid':macho_uuid(syskc),'files':planned,'dependencies':dependencies,'prepared_at':time.time()}
    (work/'plan.json').write_text(json.dumps(plan,indent=2));return plan


def current_hash(path):return sha(path) if Path(path).is_file() else None


def put(src,dst,mode,expected=None):
    dst=Path(dst)
    missing=[];parent=dst.parent
    while not parent.exists():missing.append(parent);parent=parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755);directory.chmod(0o755)
        if os.geteuid()==0:os.chown(directory,0,0)
    tmp=dst.with_name(dst.name+'.nred-pending')
    if tmp.exists():raise ValueError('Unfinished temporary file: '+str(tmp))
    try:
        shutil.copyfile(src,tmp);os.chmod(tmp,mode)
        if os.geteuid()==0:os.chown(tmp,0,0)
        if sha(tmp)!=(expected or sha(src)):raise ValueError('Copy verification failed')
        os.replace(tmp,dst)
    finally:
        if tmp.exists():tmp.unlink()


def apply(plan,receipt,check_volumes=True):
    if check_volumes:verify_roots(plan['roots'],plan['volume_uuids'])
    if receipt.exists():raise ValueError('Receipt directory already exists')
    for dependency in plan.get('dependencies',[]):
        if current_hash(dependency['path'])!=dependency['sha256']:raise ValueError('Base system changed after collection linking')
    for row in plan['files']:
        if safe(Path(plan['roots'][row['scope']]),row['relative'])!=Path(row['target']):raise ValueError('Plan target mismatch')
        if current_hash(row['target'])!=row['before'] or sha(row['source'])!=row['after']:raise ValueError('Files changed after plan creation')
    receipt.mkdir(parents=True);(receipt/'backups').mkdir();journal={'plan':plan,'state':'backing-up','written':[]}
    def save():
        tmp=receipt/'receipt.pending';tmp.write_text(json.dumps(journal,indent=2));os.replace(tmp,receipt/'receipt.json')
    save()
    for i,row in enumerate(plan['files']):
        if row['before'] is not None:
            backup=receipt/'backups'/str(i);shutil.copyfile(row['target'],backup)
            if sha(backup)!=row['before']:raise ValueError('Backup verification failed')
    journal['state']='applying';save()
    try:
        for i,row in enumerate(plan['files']):
            # Journal intent before rename; recovery accepts only before/after states.
            if current_hash(row['target'])!=row['before']:raise ValueError('Destination changed during installation')
            journal['written'].append(i);save();put(row['source'],row['target'],row['mode'],row['after'])
        journal['state']='installed';save();os.sync()
    except BaseException:
        rollback(receipt,check_volumes=False);raise
    return journal


def rollback(receipt,check_volumes=True):
    journal=json.loads((receipt/'receipt.json').read_text());plan=journal['plan']
    if check_volumes:verify_roots(plan['roots'],plan['volume_uuids'])
    for i in journal['written']:
        row=plan['files'][i]
        if safe(Path(plan['roots'][row['scope']]),row['relative'])!=Path(row['target']):raise ValueError('Receipt target mismatch')
        if current_hash(row['target']) not in (row['before'],row['after']):raise ValueError('Refusing to overwrite a subsequently modified file: '+row['relative'])
        if row['before'] is not None and sha(receipt/'backups'/str(i))!=row['before']:raise ValueError('Backup changed')
    for i in reversed(journal['written']):
        row=plan['files'][i];target=Path(row['target'])
        if row['before'] is None:
            if target.exists():target.unlink()
        else:put(receipt/'backups'/str(i),target,row['mode_before'],row['before'])
    journal['state']='rolled-back';(receipt/'receipt.json').write_text(json.dumps(journal,indent=2));os.sync()
    return journal


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    for action in ['doctor','prepare','setup']:
        s=sub.add_parser(action);s.add_argument('--package',type=Path,default=HERE);s.add_argument('--system',type=Path,required=True);s.add_argument('--data',type=Path);s.add_argument('--preboot',type=Path);s.add_argument('--efi',type=Path,required=True);s.add_argument('--device-id')
        if action!='doctor':s.add_argument('--work',type=Path,required=True)
        if action=='setup':s.add_argument('--receipt',type=Path,required=True)
    s=sub.add_parser('install');s.add_argument('--plan',type=Path,required=True);s.add_argument('--receipt',type=Path,required=True)
    for action in ['verify','rollback']:
        s=sub.add_parser(action);s.add_argument('--receipt',type=Path,required=True)
    args=p.parse_args()
    if args.action in ['doctor','prepare','setup']:
        if args.data is None or args.preboot is None:
            data,preboot=discover_group(args.system);args.data=args.data or data;args.preboot=args.preboot or preboot
        roots={k:str(getattr(args,k).resolve()) for k in ['system','data','preboot','efi']};device=device_id(args.device_id);manifest,_=load_package(args.package.resolve(),device);inspect(roots,manifest)
        ids=identities(roots);verify_roots(roots,ids)
        if args.action=='doctor':print(json.dumps({'prerequisites':'ready','profile':manifest['profile']['name'],'device':device,'missing_apple_graphics_libraries':[n for n in ['AMDRadeonX5000.kext','AMDRadeonX6000Framebuffer.kext','AMDRadeonX5000MTLDriver.bundle','AMDShared.bundle'] if not (Path(roots['system'])/'System/Library/Extensions'/n).exists()]},indent=2));return
        if os.geteuid()!=0:raise ValueError('Preparation requires sudo for kmutil bundle ownership; it writes only the work directory')
        plan=build_plan(args.package.resolve(),roots,device,args.work.resolve());print('Prepared',len(plan['files']),'files:',args.work/'plan.json')
        if args.action=='prepare':return
    elif args.action=='install':plan=json.loads(args.plan.read_text())
    elif args.action=='verify':
        j=json.loads((args.receipt/'receipt.json').read_text());bad=[r['relative'] for r in j['plan']['files'] if current_hash(r['target'])!=r['after']];print(json.dumps({'receipt_state':j['state'],'changed_or_missing':bad,'runtime_log':str(Path(j['plan']['roots']['data'])/'private/var/log/nootedred-metal.log'),'note':'File installation is not proof of GPU acceleration. Read the runtime probe after a manual boot.'},indent=2));return
    if os.geteuid()!=0:raise ValueError('Installation/rollback requires sudo')
    roots=plan['roots'] if args.action!='rollback' else json.loads((args.receipt/'receipt.json').read_text())['plan']['roots']
    # Shared short lease on the selected data volume; never erase another owner's lock.
    lock=Path(roots['data'])/'Library/Application Support/NootedRed/graphics-install.lock';lock.parent.mkdir(parents=True,exist_ok=True);lock.mkdir()
    try:
        subprocess.run(['mount','-uw',roots['system']],check=True)
        result=rollback(args.receipt.resolve()) if args.action=='rollback' else apply(plan,args.receipt.resolve())
        print(result['state']+'. No reboot performed. Keep the receipt for rollback.')
    finally:lock.rmdir()

if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,StopIteration,OSError,subprocess.CalledProcessError) as error:
        print('STOP:',error,file=sys.stderr);sys.exit(1)
