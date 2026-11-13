"""测量 exec 延迟：对比共享缓存补丁对普通命令与 GPU 客户端启动时间的影响。"""
import subprocess, time, sys
cmds = {'true (/usr/bin/true)': ['/usr/bin/true'], 'ls (/bin/ls)': ['/bin/ls', '/'], 'sips (CoreImage, GPU)': ['/usr/bin/sips', '--help']}
for name, cmd in cmds.items():
    t = []
    for _ in range(40):
        s = time.perf_counter(); subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); t.append((time.perf_counter() - s) * 1000)
    t.sort(); print(f'{name:24} p50={t[20]:6.1f} ms  p90={t[36]:6.1f} ms  min={t[0]:6.1f} ms')
