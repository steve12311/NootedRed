"""执行生产 Metal Hook，验证组级准入、守卫先行、幂等及失败时停止渲染。"""
from pathlib import Path
import argparse
import re
import subprocess

from test_legacy_bridges import COMMON
from user_patch_memory_harness import with_memory_helpers

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/display-runtime-bridge'

MAIN = r'''
static void put(uint64_t address,const uint8_t* bytes,unsigned size){
 for(unsigned i=0;i<size;i++){
  const auto page=(address+i)&~uint64_t(4095);
  memory[page][(address+i)&4095]=bytes[i];protections[page]=maximums[page]=5;
 }
}
static uint64_t methodAddress(){
 return cacheBase+(hostMajor==23?UserLegacyData::submitOffset(23):UserSubmitGuardData::MethodOffset);
}
static void setup(){
 reset(legacyProfile);
 if(!legacyProfile){
  for(unsigned i=0;i<arrsize(UserSurfaceSyncData::ComputeScratchPatches);i++){
   const auto& patch=devicePatches->scratch[i];put(cacheBase+patch.offset,patch.original,patch.size);
  }
 }
 put(methodAddress(),UserSubmitGuardData::MethodOriginal,UserSubmitGuardData::MethodPatchSize);
}
static void renderBytes(bool patched){
 if(legacyProfile){check(legacyProfile,patched);return;}
 for(unsigned i=0;i<arrsize(UserSurfaceSyncData::ComputeScratchPatches);i++){
  const auto& patch=devicePatches->scratch[i];uint8_t bytes[512];
  assert(reader(cacheBase+patch.offset,bytes,patch.size)==0);
  assert(memcmp(bytes,patched?patch.patched:patch.original,patch.size)==0);
 }
}
int main(){
 using VPF::DisplayGroupState;
 for(unsigned major:{23U,25U})for(unsigned device:{0x15D8U,0x15DDU,0x15E7U,0x1636U,0x1638U,0x164CU}){
  hostMajor=major;NRed::singleton().device=device;wantRender=true;
  KernelPatcher kernel;UserSurfaceSync::init(kernel);assert(kernel.routes==1);
  setup();call();renderBytes(true);rx();
  assert(allocations==1&&deallocations==0);
  assert(properties.at("NRedDisplayRenderGroupState")==unsigned(DisplayGroupState::Installed));
  assert(properties.at("NRedDisplaySubmitGroupState")==unsigned(DisplayGroupState::Installed));
  assert(properties.at("NRedDisplayScratchGroupState")
         ==unsigned(major==23?DisplayGroupState::Disabled:DisplayGroupState::Installed));
  auto before=writes;call();assert(writes==before&&allocations==1);renderBytes(true);rx();

  // 守卫分配/入口失败时不能继续写渲染计划。
  setup();failAllocate=true;call();renderBytes(false);rx();assert(writes==0);
  assert(properties.at("NRedDisplayRenderGroupState")==unsigned(DisplayGroupState::RollbackComplete));
  for(unsigned failure:{1U,2U}){
   setup();failWrite={failure};call();renderBytes(false);rx();assert(allocations==deallocations);
   assert(!properties.contains("NRedImmediateSyncApplied"));
  }
  // 载荷和入口已完成后才写渲染；渲染回滚不能把安全组误报为失败或新渲染已生效。
  setup();failWrite={3};call();renderBytes(false);rx();
  assert(properties.at("NRedDisplayRenderGroupState")==unsigned(DisplayGroupState::RollbackComplete));
  assert(properties.at("NRedDisplaySubmitGroupState")==unsigned(DisplayGroupState::Installed));
  assert(!properties.contains("NRedImmediateSyncApplied"));

  setup();memory.at(methodAddress()&~uint64_t(4095))[methodAddress()&4095]^=1;
  call();renderBytes(false);rx();assert(writes==0&&allocations==0);
  assert(properties.at("NRedDisplayPlannerRejected")!=0);

  setup();const auto patch=legacyProfile?legacyProfile->patches[0].offset:devicePatches->scratch[0].offset;
  memory.at((cacheBase+patch)&~uint64_t(4095))[(cacheBase+patch)&4095]^=1;
  call();assert(writes==0&&allocations==0&&protects==0);
 }
 puts("PASS actual Metal Display Planner: 12 OS/device profiles, guard-before-render, rejection, rollback, group state and idempotency");
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    stubs = WORK / 'stubs'
    (stubs / 'IOKit').mkdir(parents=True, exist_ok=True)
    (stubs / 'IOKit/IOTypes.h').write_text(
        '#pragma once\n#include <cstdint>\n'
        'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    (stubs / 'Headers').mkdir(exist_ok=True)
    (stubs / 'Headers/kern_util.hpp').write_text('#pragma once\n#include <IOKit/IOTypes.h>\n')
    source = (ROOT / 'NootedRed/UserSurfaceSync.cpp').read_text()
    body = re.split(r'namespace\s*\{', source, maxsplit=1)[1]
    body = re.split(r'}\s*// namespace', body, maxsplit=1)[0]
    body = with_memory_helpers(body)
    init = source[source.index('void UserSurfaceSync::init('):]
    text = COMMON.replace('#include <VPF/CapabilityState.hpp>', '#include <VPF/DisplayRuntime.hpp>')
    text = text.replace('is_submap=0;};',
                        'is_submap=0;unsigned object_id=0;unsigned char share_mode=0;unsigned short shadow_depth=0;};')
    text = text.replace('namespace UserSurfaceSync {void init(struct KernelPatcher&);}',
                        'struct KernelPatcher;namespace UserSurfaceSync {void init(KernelPatcher&);}')
    text = text.replace('BODY', body).replace('INIT', init).replace('MAIN', MAIN)
    path, exe = WORK / 'bridge.cpp', WORK / 'bridge'
    path.write_text(text)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if args.sanitize else []
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', *flags, '-I'+str(stubs),
                    '-I'+str(ROOT / 'NootedRed'), str(path), '-o', str(exe)], check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
