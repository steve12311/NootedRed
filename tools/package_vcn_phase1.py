"""打包第一阶段离线候选与可核验依据；不安装、不重启、不运行硬件用例。"""
import datetime
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import zipfile
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'build/vcn-capabilities'
CONFIGS=['Debug','Research Release','Release']
LOGS=['final-native-code','final-guard24','final-guard25','final-section','final-surface-regression',
      'final-legacy-bridge','final-hooks','final-memory','final-multi-os','final-native-load',
      'final-kernel-install','final-kernel-abi','final-hardware-gate','final-stack',
      'final-package-flags','final-real-cache26']


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_log(name):
    path=EVIDENCE/(name+'.log');text=path.read_text()
    if 'PASS' not in text or any(word in text for word in ['Traceback','Assertion failed','error:','ERROR: AddressSanitizer','runtime error:']):
        raise RuntimeError(f'回归日志未通过：{path}')
    return path


def payload(major):
    text=(ROOT/f'NootedRed/UserVideoGuard{major}Data.hpp').read_text()
    assert 'UInt32 Features = 0;' in text
    match=re.search(r'UInt8 Payload\[\] = \{(.*?)\};',text,re.S)
    return bytes(int(value,16) for value in re.findall(r'0x([0-9a-fA-F]{2})',match[1]))


def main():
    base=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    builds=json.loads((EVIDENCE/'build-results.json').read_text())
    assert len(builds)==6 and {(r['configuration'],r['action']) for r in builds}=={(c,a) for c in CONFIGS for a in ['build','analyze']}
    assert all(r['returncode']==0 and not r['analyzerHTML'] for r in builds)
    logs=[checked_log(name) for name in LOGS]
    for major in [24,25]:
        path=checked_log(f'release-bridge{major}');text=path.read_text()
        begins=set(re.findall(r'BEGIN Darwin \d+ device=(\w+) group=(\d+)',text))
        expected={(device,str(group)) for device in ['15e7','1636','1638','164c'] for group in range(12 if device=='1638' else 11)}
        assert begins==expected and text.count('PASS ')==45
        logs.append(path)
    generator=(EVIDENCE/'final-generator25.log').read_text()
    assert 'Generated Darwin 25: 4 guarded entries, 782 payload bytes, features=0' in generator
    reports=[]
    for config in CONFIGS:
        report=ROOT/'build/vcn-stack'/config.lower().replace(' ','-')/'report.json'
        data=json.loads(report.read_text());assert not data['largeFrames']
        assert data['maxFrame']<=2048 and all(r['bytes']<=8192 for r in data['paths'])
        reports.append(report)
    stamp=datetime.datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d-%H%M%S')
    destination=ROOT/'build/vcn-phase1'/f'NootedRed-VCN2-Phase1-{stamp}'
    destination.mkdir(parents=True,exist_ok=False)
    for name in ['packages','evidence','source']: (destination/name).mkdir()
    baseline_libraries=plistlib.loads((ROOT/'NootedRed/Info.plist').read_bytes())['OSBundleLibraries']
    binaries=[]
    for config in CONFIGS:
        product=ROOT/'Build/Products'/config;kext=product/'NootedRed.kext'
        info=plistlib.loads((kext/'Contents/Info.plist').read_bytes())
        assert info['OSBundleLibraries']==baseline_libraries
        binary=kext/'Contents/MacOS/NootedRed';data=binary.read_bytes()
        for marker in ['NRedVCNEncodeGuardRevision','NRedVCNDecodeGuardRevision','NRedVCNGuardedEncoderPID','NRedVCNUserSafetyProfileMissing','NRedVCNGuardRollbackRetainedPage']:
            assert marker.encode()+b'\0' in data,(config,marker)
        for major in [24,25]:assert payload(major) in data,(config,major,'payload')
        archives=list(product.glob('NootedRed-*.zip'));assert len(archives)==1
        archive=archives[0]
        with zipfile.ZipFile(archive) as check:
            assert check.testzip() is None
            stored=check.read('NootedRed.kext/Contents/MacOS/NootedRed')
            assert hashlib.sha256(stored).hexdigest()==digest(binary)
        shutil.copy2(archive,destination/'packages'/archive.name)
        binaries.append({'configuration':config,'package':archive.name,'binarySHA256':digest(binary),'packageSHA256':digest(archive),'size':len(data)})
    changed=subprocess.check_output(['git','diff','--name-only','HEAD'],cwd=ROOT,text=True).splitlines()
    untracked=subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    sources=sorted(set(p for p in changed+untracked if p.startswith(('NootedRed/','tools/','docs/')) and '__pycache__' not in p))
    patch=subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT)
    for relative in untracked:
        if relative in sources:
            addition=subprocess.run(['git','diff','--no-index','--binary','--','/dev/null',relative],cwd=ROOT,capture_output=True)
            assert addition.returncode==1
            patch+=addition.stdout
    (destination/'source.patch').write_bytes(patch)
    subprocess.run(['git','apply','--reverse','--check',str(destination/'source.patch')],cwd=ROOT,check=True)
    for relative in sources:
        target=destination/'source'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/relative,target)
    for log in logs:shutil.copy2(log,destination/'evidence'/log.name)
    for result in ['build-results.json','final-generator25.log','previous-live-cache-evidence.txt']:
        shutil.copy2(EVIDENCE/result,destination/'evidence'/result)
    for row in builds:
        source=ROOT/row['log'];shutil.copy2(source,destination/'evidence'/source.name)
    for report in reports:
        shutil.copy2(report,destination/'evidence'/f'stack-{report.parent.name}.json')
    shutil.copy2(ROOT/'docs/vcn2-phase1.md',destination/'README.md')
    manifest={'baseline':base,'createdAt':datetime.datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),
              'scope':'phase1-safety-only','guardRevision':1,'features':0,'hardwareTests':'not_run',
              'installed':False,'rebooted':False,'devices':['15E7','1636','1638','164C'],
              'systems':{'Darwin23':'fail_closed_missing_full_safety_evidence','Darwin24':'offline_pass_hardware_unverified','Darwin25':'offline_pass_hardware_unverified'},
              'cacheEvidence':{'Darwin24':'42ec2890bc40317ebb2745142bff7f26; previous real-cache run + final native/generated-data checks; source volume currently unavailable',
                               'Darwin25':'21fb8708fc8e3765be8c65d612cdf049; final real-cache run'},
              'entries':['AvdCapability::checkCapabilityList','AvdAvcDecDataProcessor::SetDecodeMessageFields','VAUveEncoder::EncodePicture','VAUveEncoder::configure'],
              'matchingContext':'Vcn2Encoder C1 + full adjacent init; per-source section targets; all original references verified',
              'binaryPackages':binaries,'sourceFiles':{p:digest(ROOT/p) for p in sources},
              'submodules':{name:subprocess.check_output(['git','-C',str(ROOT/name),'rev-parse','HEAD'],text=True).strip()
                            for name in ['Lilu','MacKernelSDK']},
              'limitations':['No GPU execution, installation or reboot in this validation run','Kernel/VM/IOKit internal stack is outside the measured kext-visible stack path','macOS14 safety profile is unavailable and the user adaptation is disabled']}
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
    hashes=''.join(f'{digest(p)}  {p.relative_to(destination)}\n' for p in sorted(destination.rglob('*')) if p.is_file())
    (destination/'SHA256SUMS').write_text(hashes)
    archive=destination.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(destination.rglob('*')):
            if path.is_file():output.write(path,path.relative_to(destination))
    with zipfile.ZipFile(archive) as check:assert check.testzip() is None
    archive.with_suffix('.zip.sha256').write_text(f'{digest(archive)}  {archive.name}\n')
    print(json.dumps({'archive':str(archive),'sha256':digest(archive),'size':archive.stat().st_size,'directory':str(destination)},ensure_ascii=False))


if __name__=='__main__':main()
