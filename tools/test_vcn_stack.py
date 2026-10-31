"""按实际 kext 编译选项测量栈帧及保守的内部调用链上界，不执行 GPU 工作。"""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
FRAME_LIMIT=2048
PATH_LIMIT=8192
WRAPPER_LIMIT=512


def symbols(text):
    return [a or b for a,b in re.findall(r'@(?:"([^"]+)"|([^\s(]+))\(',text)]


def graph(text):
    result={};current=None
    for line in text.splitlines():
        if line.startswith('define '):
            current=symbols(line)[0];result[current]=set()
        elif line=='}':current=None
        elif current and re.search(r'\b(call|invoke)\b',line):
            result[current].update(symbols(line))
    return result


def measure(configuration,work):
    directories=[ROOT/path/configuration/'NootedRed.build/Objects-normal/x86_64'
                 for path in ['Build/Intermediates.noindex/NootedRed.build','build/NootedRed.build']]
    responses=[p for directory in directories for p in directory.glob('*-common-args.resp')
               if '-mkernel' in p.read_text() and '-std=c++23' in p.read_text()
               and '__clang_analyzer__' not in p.read_text()]
    if not responses:raise RuntimeError(f'请先构建 {configuration}，以获得生产编译选项')
    response=responses[0]
    sdk=subprocess.check_output(['xcrun','--show-sdk-path'],text=True,timeout=10).strip()
    work.mkdir(parents=True,exist_ok=True)
    nodes={};edges={};commands=[]
    for module in ['UserVideoDecode','UserSurfaceSync']:
        prefix=work/module
        common=['xcrun','clang++','@'+str(response),'-target','x86_64-apple-macos10.14','-isysroot',sdk]
        source=str(ROOT/f'NootedRed/{module}.cpp')
        for options in [['-c','-fstack-usage',source,'-o',str(prefix.with_suffix('.o'))],
                        ['-S','-emit-llvm',source,'-o',str(prefix.with_suffix('.ll'))]]:
            command=common+options;commands.append(command)
            with prefix.with_suffix('.compile.log').open('a') as log:
                subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60,cwd=ROOT)
        local={}
        for line in prefix.with_suffix('.su').read_text().splitlines():
            name,size,kind=line.split('\t');name=name.rsplit(':',1)[-1]
            if kind!='static':raise AssertionError(f'动态栈帧不能给出固定上界：{name}: {kind}')
            local[name]=int(size)
        for name,size in local.items():nodes[module+'::'+name]=size
        for name,children in graph(prefix.with_suffix('.ll').read_text()).items():
            edges[module+'::'+name]={module+'::'+child for child in children if child in local}
        # 生产代码唯一的上下文读取回调是 overlayReader；把这个间接调用也计入上界。
        overlays=[name for name in local if 'overlayReader' in name]
        for name in local:
            if 'UserSharedCache5Input4read' in name:
                edges.setdefault(module+'::'+name,set()).update(module+'::'+child for child in overlays)
    # Planner 的访问状态限制递归深度：最多 32 个特性，再加一次环检测调用。
    # 只折叠该函数的自调用；任何其他递归仍报错，单帧与路径阈值不变。
    planner=(ROOT/'NootedRed/VPF/DependencyPlanner.hpp').read_text()
    assert 'static_assert(FeatureCount <= 32);' in planner
    bounded={name:33 for name in nodes
             if name.split('::',1)[1].startswith('_ZZN3VPF8evaluate')
             and 'EEN8Resolver7resolveENS_9FeatureIdE' in name
             and name in edges.get(name,set())}
    # GroupClosure::resolve 用 7 个显示组的访问状态截断递归，最多 7 层加一次环检测。
    manifest=(ROOT/'NootedRed/VPF/DisplayManifest.hpp').read_text()
    assert 'constexpr UInt32 DisplayGroupCount = 7;' in manifest
    bounded.update({name:8 for name in nodes
                    if 'DisplayPlannerDetail12GroupClosure7resolve' in name
                    and name in edges.get(name,set())})
    memo={}
    def longest(name,parents=frozenset()):
        if name in parents:raise AssertionError('检测到递归内部调用链：'+name)
        if name in memo:return memo[name]
        candidates=[longest(child,parents|{name}) for child in edges.get(name,[])
                    if child in nodes and not (child==name and name in bounded)]
        best=max(candidates,key=lambda value:value[0],default=(0,[]))
        value=(nodes[name]*bounded.get(name,1)+best[0],[name]+best[1]);memo[name]=value;return value
    roots=[name for name in nodes if 'wrappedSharedRegionCheck' in name]
    if len(roots)!=2:raise AssertionError(f'钩子数量异常：{roots}')
    paths=[]
    for root in roots:
        total,path=longest(root)
        # 间接 originalCheck 可能链到另一钩子；原始调用返回后才进入补丁，
        # 因而仅额外叠加外层 wrapper，不能把两个补丁助手同时相加。
        outer=max(nodes[other] for other in roots if other!=root)
        paths.append({'root':root,'bytes':total+outer,'outerWrapperBytes':outer,
                      'chain':[{'symbol':name,'bytes':nodes[name], 'maxDepth':bounded.get(name,1)} for name in path]})
    report={'configuration':configuration,'frameLimit':FRAME_LIMIT,'pathLimit':PATH_LIMIT,
            'wrapperLimit':WRAPPER_LIMIT,'maxFrame':max(nodes.values()),
            'largeFrames':[{'symbol':n,'bytes':s} for n,s in nodes.items() if s>FRAME_LIMIT],
            'wrappers':{n:nodes[n] for n in roots},'paths':paths,'commands':commands,
            'boundedRecursion':bounded,
            'scope':'生产 x86_64 kext 的静态栈帧及可见内部调用；内核系统调用、copyin/copyout、VM/IOKit 和锁实现的栈不计入，另留至少 8 KiB 余量。'}
    (work/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    if report['largeFrames']:raise AssertionError(report['largeFrames'])
    assert all(nodes[root]<=WRAPPER_LIMIT for root in roots),report['wrappers']
    assert all(path['bytes']<=PATH_LIMIT for path in paths),paths
    print(f'PASS {configuration}: max frame={report["maxFrame"]}, combined hook path={max(p["bytes"] for p in paths)}, wrapper max={max(nodes[r] for r in roots)} bytes',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configuration',choices=['Debug','Research Release','Release'],action='append')
    args=parser.parse_args()
    for config in args.configuration or ['Debug','Research Release','Release']:
        measure(config,ROOT/'build/vcn-stack'/config.lower().replace(' ','-'))


if __name__=='__main__':main()
