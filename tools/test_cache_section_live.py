"""只读真实共享缓存，验证显示、视频和提交守卫计划，不创建 Metal 设备。"""
import argparse
from pathlib import Path
import struct
import subprocess

from metal_cache import CacheReader

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/cache-section-targets/live'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=Path('/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld'))
    parser.add_argument('--major',type=int,choices=[23,24,25],default=25)
    parser.add_argument('--rdd-only', action='store_true', help='仅核验 VideoToolbox 解码候选')
    parser.add_argument('--display-only', action='store_true', help='仅验证显示与提交守卫，不加载视频版本数据')
    parser.add_argument('--repeat', type=int, default=1, choices=range(1, 21))
    parser.add_argument('--label', default='result')
    parser.add_argument('--kernel-memchr', action='store_true')
    parser.add_argument('--memory-reader', action='store_true', help='映射缓存文件后复制读取，减少 pread 对 CPU 对照的干扰')
    parser.add_argument('--kernel-codegen', action='store_true', help='禁用 SIMD 编译，使用整数微秒报告 CPU 耗时')
    parser.add_argument('--baseline-ref', help='从指定本地提交读取解析器源码，供相同缓存的 CPU 对照测量')
    args = parser.parse_args()
    if args.rdd_only and args.baseline_ref:
        parser.error('--rdd-only 需要当前解析器')
    WORK.mkdir(parents=True, exist_ok=True)
    with (args.cache / 'dyld_shared_cache_x86_64h').open('rb') as handle:
        handle.seek(88)
        uuid = handle.read(16).hex()
    cache = CacheReader(args.cache, uuid)
    mappings = []
    try:
        for start, end, offset, handle in cache.mappings:
            handle.seek(312)
            position, count = struct.unpack('<II', handle.read(8))
            handle.seek(position)
            records = handle.read(count * 56)
            mask = add = 0
            for i in range(count):
                address, _, _, slide, size, *_ = struct.unpack_from('<QQQQQQII', records, i * 56)
                if address == start and size:
                    handle.seek(slide)
                    info = handle.read(40)
                    if struct.unpack_from('<I', info)[0] != 2:
                        raise ValueError('需 slide_info2 参考数据')
                    mask, add = struct.unpack_from('<QQ', info, 24)
            mappings.append(f'{{0x{start:X}ULL,0x{end:X}ULL,{offset},0x{mask:X}ULL,0x{add:X}ULL,"{handle.name}"}}')
    finally:
        cache.close()
    source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>
#include <ctime>
#if TEST_KERNEL_MEMCHR
// 与本机内核 _memchr 的有符号字节提升行为一致。
static void* kernelMemchr(const void* source,int value,size_t count){
 const auto* bytes=static_cast<const signed char*>(source);
 for(size_t i=0;i<count;i++)if(bytes[i]==value)return const_cast<signed char*>(bytes+i);
 return nullptr;
}
#define memchr kernelMemchr
#endif
#include <UserSurfaceSyncResolver.hpp>
#include <UserVideoDecodeResolver.hpp>
struct Mapping {uint64_t start,end,offset,mask,add;const char* path;int fd=-1;};
static Mapping mappings[]={MAPPINGS};
static unsigned calls=0;static uint64_t bytesRead=0;
static int readReference(uint64_t address,void* bytes,size_t length){
 calls++;bytesRead+=length;
 for(auto&m:mappings)if(m.start<=address&&address<m.end&&length<=m.end-address){
  if(m.fd<0)m.fd=open(m.path,O_RDONLY);if(m.fd<0)return 1;
  if(pread(m.fd,bytes,length,m.offset+address-m.start)!=ssize_t(length))return 1;
  if(length==8&&m.mask){uint64_t value;memcpy(&value,bytes,8);value&=~m.mask;if(value)value+=m.add;memcpy(bytes,&value,8);}
  return 0;
 }
 return 1;
}
#if RDD_ONLY
static const UserVideoDecodeResolver::RDDPlan* installed;
#endif
static int reader(uint64_t address, void* bytes, size_t length) {
 int status=readReference(address,bytes,length);
#if RDD_ONLY
 if(status==0&&installed)for(unsigned i=0;i<installed->count;++i){
  const auto& site=installed->patches[i];
  const auto start=UserSurfaceSyncData::CacheBase+site.offset;
  const auto first=address>start?address:start;
  const auto end=address+length<start+site.size?address+length:start+site.size;
  if(first<end)memcpy(static_cast<uint8_t*>(bytes)+first-address,site.patched+first-start,end-first);
 }
#endif
 return status;
}
template<class Plan>static void originals(const Plan& plan,unsigned count){
 for(unsigned i=0;i<count;i++){
  auto&p=plan.patches[i];uint8_t bytes[512];assert(p.size>0&&p.size<=sizeof(bytes));
  assert(reader(UserSurfaceSyncData::CacheBase+p.offset,bytes,p.size)==0);
  assert(memcmp(bytes,p.original,p.size)==0);
 }
}
int main(){
 auto base=UserSurfaceSyncData::CacheBase;
#if RDD_ONLY
 UserVideoDecodeResolver::RDDPlan candidates;
 UserSharedCache::Input input{reader,{}};
 uint8_t scan[UserSharedCache::Input::ScanSize];
 input.scanBuffer=scan;
 assert(UserVideoDecodeResolver::resolveRDD(input,base,candidates,MAJOR));
 assert(candidates.count==(MAJOR==23?2:4));
 originals(candidates,candidates.count);
 for(unsigned i=0;i<candidates.count;++i){
  auto& site=candidates.patches[i];
  printf("PASS real RDD site=%llx size=%u\n",site.offset,site.size);
 }
 printf("PASS real RDD Darwin %u reads=%u bytes=%u\n",MAJOR,input.reads,input.bytes);
 installed=&candidates;
 UserVideoDecodeResolver::RDDPlan repeated;
 UserSharedCache::Input installedInput{reader,{}};
 installedInput.scanBuffer=scan;
 assert(UserVideoDecodeResolver::resolveRDD(installedInput,base,repeated,MAJOR));
 assert(repeated.count==candidates.count);
 for(unsigned i=0;i<repeated.count;++i){
  const auto& initial=candidates.patches[i];
  const auto& after=repeated.patches[i];
  assert(repeated.locations[i].patched&&after.offset==initial.offset&&after.size==initial.size);
  assert(memcmp(after.original,initial.original,after.size)==0);
  assert(memcmp(after.patched,initial.patched,after.size)==0);
 }
 installed=nullptr;
 puts("PASS real RDD repeated installation proof");
#else
 for(unsigned repeat=0;repeat<REPEAT;repeat++){
 for(auto&device:SURFACE_DATA::DevicePatches){
  calls=0;bytesRead=0;auto start=clock();
  UserSurfaceSyncResolver::Plan plan;
  plan.input={reader,{}};plan.input.scanBuffer=plan.scan;
  assert(UserSurfaceSyncResolver::resolveMetal(plan.input,base,device.deviceID,plan,MAJOR));
  printf("PERF surface device=%x calls=%u bytes=%llu cpu_ms=%.3f\n",device.deviceID,calls,bytesRead,1000.0*(clock()-start)/CLOCKS_PER_SEC);
  originals(plan,4);printf("PASS real surface device=%x reads=%u\n",device.deviceID,plan.reads);
 }
 if(!DISPLAY_ONLY)for(auto&device:VIDEO_DATA::DevicePatches){
  calls=0;bytesRead=0;auto start=clock();
  UserVideoDecodeResolver::Plan plan;
  UserSharedCache::Input input{reader,{}};
#if OPTIMIZED
  uint8_t scan[UserSharedCache::Input::ScanSize];input.scanBuffer=scan;
#endif
  assert(UserVideoDecodeResolver::resolve(input,base,device.patches,plan,MAJOR));
  originals(plan,sizeof(plan.patches)/sizeof(plan.patches[0]));
  printf("PASS real video device=%x reads=%u bytes=%u\n",device.deviceID,plan.reads,input.bytes);
  printf("PERF video device=%x calls=%u bytes=%llu cpu_ms=%.3f\n",device.deviceID,calls,bytesRead,1000.0*(clock()-start)/CLOCKS_PER_SEC);
 }
 {calls=0;bytesRead=0;auto start=clock();uint64_t offset=0;UserSharedCache::Input input{reader,{}};
 assert(UserSurfaceSyncResolver::resolveSubmitForOS(input,base,offset,MAJOR));
 printf("PASS real full submit method offset=%llx\n",offset);
 printf("PERF submit calls=%u bytes=%llu cpu_ms=%.3f\n",calls,bytesRead,1000.0*(clock()-start)/CLOCKS_PER_SEC);}
#if OPTIMIZED
 {
  calls=0;bytesRead=0;auto start=clock();uint64_t offset=0;
  UserSurfaceSyncResolver::Plan plan;auto& input=plan.input;input.reader=reader;input.scanBuffer=plan.scan;
  using namespace RESOLVER_DATA;
  input.selectImages(base,ResolverImages,sizeof(ResolverImages)/sizeof(ResolverImages[0]));
  assert(UserSurfaceSyncResolver::resolveSubmitForOS(input,base,offset,MAJOR));
  assert(UserSurfaceSyncResolver::resolveMetal(input,base,SURFACE_DATA::DevicePatches[0].deviceID,plan,MAJOR));
  printf("PERF display+submit calls=%u bytes=%llu cpu_ms=%.3f\n",calls,bytesRead,1000.0*(clock()-start)/CLOCKS_PER_SEC);
  assert(input.reads<32768&&input.bytes<32*1024*1024);originals(plan,4);
 }
#endif
 }
#endif
 for(auto&m:mappings)if(m.fd>=0)close(m.fd);
}
'''.replace('MAPPINGS', ',\n'.join(mappings)).replace('MAJOR',str(args.major)).replace('REPEAT', str(args.repeat)).replace('VIDEO_DATA',{23: 'UserSonomaVideoData', 24: 'UserSequoiaVideoData', 25: 'UserVideoDecodeData'}[args.major])
    source = source.replace('SURFACE_DATA', {23: 'UserSonomaSurfaceData', 24: 'UserSequoiaSurfaceData', 25: 'UserSurfaceSyncData'}[args.major])
    source = source.replace('RESOLVER_DATA', {23: 'UserSonomaSurfaceData', 24: 'UserSequoiaSurfaceData',
                                               25: 'UserSurfaceSyncData::ScratchResolverData'}[args.major])
    source = source.replace('DISPLAY_ONLY', str(int(args.display_only))).replace('RDD_ONLY', str(int(args.rdd_only)))
    if args.memory_reader:
        source = source.replace('#include <ctime>', '#include <ctime>\n#include <sys/mman.h>\n#include <sys/stat.h>')
        source = source.replace('int fd=-1;', 'int fd=-1;void* memory=nullptr;size_t fileSize=0;')
        source = source.replace(
            'if(pread(m.fd,bytes,length,m.offset+address-m.start)!=ssize_t(length))return 1;',
            '''if(!m.memory){
   struct stat st{};if(fstat(m.fd,&st)!=0||st.st_size<=0)return 1;m.fileSize=st.st_size;
   m.memory=mmap(nullptr,m.fileSize,PROT_READ,MAP_PRIVATE,m.fd,0);
   if(m.memory==MAP_FAILED){m.memory=nullptr;return 1;}
  }
  auto offset=m.offset+address-m.start;
  if(offset>m.fileSize||length>m.fileSize-offset)return 1;
  memcpy(bytes,static_cast<const uint8_t*>(m.memory)+offset,length);''')
        source = source.replace('if(m.fd>=0)close(m.fd);',
                                'if(m.fd>=0){if(m.memory)munmap(m.memory,m.fileSize);close(m.fd);}')
    if args.kernel_codegen:
        source = source.replace('cpu_ms=%.3f', 'cpu_us=%llu').replace(
            '1000.0*(clock()-start)/CLOCKS_PER_SEC',
            'static_cast<unsigned long long>(1000000ULL*(clock()-start)/CLOCKS_PER_SEC)')
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    if Path(args.label).name != args.label:
        raise ValueError('label 必须为文件名')
    file, executable = WORK / f'{args.label}.cpp', WORK / args.label
    file.write_text(source)
    baseline_flags = ['-DOPTIMIZED=1']
    if args.baseline_ref:
        baseline = WORK / 'baseline'
        baseline.mkdir(exist_ok=True)
        for name in ['UserCacheResolver.hpp', 'UserSharedCache.hpp', 'UserSurfaceSyncResolver.hpp',
                     'UserVideoDecodeResolver.hpp']:
            text = subprocess.check_output(['git', 'show', args.baseline_ref + ':NootedRed/' + name],
                                           cwd=ROOT, text=True, timeout=10)
            (baseline / name).write_text(text)
        buffered = 'scanBuffer' in (baseline / 'UserSharedCache.hpp').read_text()
        baseline_flags = ['-DOPTIMIZED=' + str(int(buffered)), '-I' + str(baseline)]
    codegen_flags = ['-mno-sse', '-mno-sse2', '-mno-mmx', '-fno-vectorize', '-fno-slp-vectorize'] if args.kernel_codegen else []
    subprocess.run(['clang++', '-std=c++23', '-O2', *codegen_flags, '-DTEST_KERNEL_MEMCHR=' + str(int(args.kernel_memchr)),
                    *baseline_flags, '-I' + str(WORK / 'stubs'),
                    '-I' + str(ROOT / 'NootedRed'), str(file), '-o', str(executable)], check=True, timeout=60)
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
    (WORK / (args.label + '.txt')).write_text('cacheUUID=' + uuid + '\n' + result.stdout + result.stderr)
    print(result.stdout, end='')
    print(result.stderr, end='')
    result.check_returncode()


if __name__ == '__main__':
    main()
