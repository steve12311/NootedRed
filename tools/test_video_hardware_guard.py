"""验证实机测试门禁的数据判定，不运行任何硬件用例。"""
from video_hardware_guard import guarded_service
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout
import io
import json
import run_video_encode_test
from video_hardware_guard import HardwareFault, GPUMonitor


def main():
    row={'NRedVCNDeviceID':0x1638,'NRedVCNEncodeGuardRevision':1,
         'NRedVCNDecodeGuardRevision':1,'NRedVCNGuardedEncoderPID':123,
         'NRedVCNGuardedDecoderPID':456}
    processes={123:'/System/Library/VTEncoderXPCService',456:'/System/Library/VTDecoderXPCService'}
    assert guarded_service([{'IORegistryEntryChildren':[row]}],processes)==123
    assert guarded_service([row],processes,'Decoder')==456
    cases=2
    for key,value in [('NRedVCNDeviceID',0x6863),('NRedVCNEncodeGuardRevision',0),
                      ('NRedVCNDecodeGuardRevision',0),('NRedVCNGuardedEncoderPID',-1),
                      ('NRedVCNGuardedEncoderPID',456),('NRedVCNGuardRollbackRetainedPage',1)]:
        bad=deepcopy(row);bad[key]=value
        try:guarded_service([bad],processes)
        except RuntimeError:cases+=1
        else:raise AssertionError((key,value))
    for rows,live in [([],processes),([row],{}),([row,row],processes),([row],{123:'VTEncoderXPCFake'})]:
        try:guarded_service(rows,live)
        except RuntimeError:cases+=1
        else:raise AssertionError((rows,live))
    with TemporaryDirectory() as directory:
        monitor=GPUMonitor.__new__(GPUMonitor)
        monitor.path=Path(directory)/'gpu.json';monitor.process=SimpleNamespace(poll=lambda:None)
        monitor.path.write_text('Filtering the log data using "gpuRestart"\n');monitor.check();cases+=1
        for message in ['gpuRestart channel 14','GPU Reset','WindowServer watchdog timeout']:
            monitor.path.write_text(json.dumps({'eventMessage':message}))
            try:monitor.check()
            except HardwareFault:cases+=1
            else:raise AssertionError(message)
        # 最后一项进程已经退出 0 后才发现 GPU 异常，整轮仍必须失败。
        class Process:
            returncode=0
            def __init__(self,*args,**kwargs):pass
            def wait(self,timeout):return 0
            def kill(self):pass
        class Monitor:
            count=0
            def check(self):
                self.count+=1
                if self.count==3:raise HardwareFault('gpuRestart')
        rows=[]
        with patch.object(run_video_encode_test,'require_guard',return_value=123),patch.object(run_video_encode_test.subprocess,'Popen',Process),redirect_stdout(io.StringIO()):
            result=run_video_encode_test.run_cases(SimpleNamespace(timeout=1),Path(directory),Path('unused'),
                                                   [('hardware-hevc',[])],Monitor(),rows)
        assert result==1 and rows[0]['returncode']==0 and rows[0]['hardwareFault'];cases+=1
    print(f'PASS {cases} hardware preflight cases: revision, live PID, service identity and device whitelist')


if __name__=='__main__':main()
