"""硬件回归的安装前置检查；安装证据不代表编解码功能已通过。"""
from pathlib import Path
import plistlib
import subprocess


def guarded_service(registry, processes, service='Encoder'):
    """同时核验核显、两个守卫版本以及仍在运行的实际 XPC 服务 PID。"""
    if service not in ('Encoder', 'Decoder'):
        raise ValueError('未知视频服务')
    expected=f'VT{service}XPCService'
    stack=list(registry)
    matches=[]
    while stack:
        item=stack.pop()
        if not isinstance(item,dict):
            continue
        stack.extend(item.get('IORegistryEntryChildren',[]))
        if item.get('NRedVCNDeviceID') not in (0x15e7,0x1636,0x1638,0x164c):
            continue
        if item.get('NRedVCNGuardRollbackRetainedPage'):
            raise RuntimeError('已报告不完整回滚，停止硬件测试并保留现场日志')
        if (item.get('NRedVCNEncodeGuardRevision')!=1
                or item.get('NRedVCNDecodeGuardRevision')!=1):
            continue
        pid=item.get(f'NRedVCNGuarded{service}PID')
        if isinstance(pid,int) and pid>0 and Path(processes.get(pid,'')).name==expected:
            matches.append(pid)
    if len(matches)!=1:
        raise RuntimeError(f'尚未确认当前 {expected} 已完整安装第一阶段守卫，停止硬件测试')
    return matches[0]


def require_guard(service='Encoder'):
    output=subprocess.run(['/usr/sbin/ioreg','-a','-l','-w','0','-r','-c','IOPCIDevice'],
                          capture_output=True,check=True,timeout=10).stdout
    registry=plistlib.loads(output)
    output=subprocess.run(['/bin/ps','-axo','pid=,comm='],capture_output=True,text=True,
                          check=True,timeout=10).stdout
    processes={}
    for line in output.splitlines():
        fields=line.strip().split(None,1)
        if len(fields)==2 and fields[0].isdigit():processes[int(fields[0])]=fields[1]
    return guarded_service(registry,processes,service)


class HardwareFault(RuntimeError):
    pass


class GPUMonitor:
    """实时保存 GPU/桌面异常；监视器失效也停止后续硬件用例。"""
    def __init__(self,work):
        self.path=work/'gpu-events.jsonl'
        self.output=self.path.open('w')
        self.errors=(work/'gpu-monitor.stderr.txt').open('w')
        predicate='process == "kernel" AND (eventMessage CONTAINS[c] "gpuRestart" OR eventMessage CONTAINS[c] "GPU Reset") OR eventMessage CONTAINS[c] "WindowServer watchdog"'
        self.process=subprocess.Popen(['/usr/bin/log','stream','--style','json','--predicate',predicate],
                                      stdout=self.output,stderr=self.errors)

    def check(self):
        import re
        if self.process.poll() is not None:
            raise HardwareFault('GPU 日志监视器退出，停止硬件测试')
        text=self.path.read_text(errors='replace')
        for message in re.findall(r'"eventMessage"\s*:\s*"([^"\n]*)',text):
            if any(word in message.lower() for word in ('gpurestart','gpu reset','windowserver watchdog')):
                raise HardwareFault('检测到新增 GPU 重置或桌面看门狗异常，停止全部后续测试')

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait(timeout=5)
        self.output.close();self.errors.close()


def wait_for_case(process,timeout,monitor=None):
    import time
    deadline=time.monotonic()+timeout
    while True:
        if monitor:monitor.check()
        remaining=deadline-time.monotonic()
        if remaining<=0:raise subprocess.TimeoutExpired(process.args,timeout)
        try:
            result=process.wait(timeout=min(0.25,remaining))
            if monitor:monitor.check()
            return result
        except subprocess.TimeoutExpired:
            continue
