// 只检查当前进程的共享缓存映射，不创建 GPU 设备或提交命令。
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static double seconds(void)
{
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return value.tv_sec + value.tv_nsec / 1000000000.0;
}

int main(void)
{
    for (unsigned i = 0; i < 8; ++i) {
        uint64_t base    = 0;
        double   start   = seconds();
        int      result  = syscall(SYS_shared_region_check_np, &base);
        double   elapsed = (seconds() - start) * 1000.0;
        if (result != 0 || base == 0) {
            fprintf(stderr, "shared_region_check_np failed: result=%d errno=%d\n", result, errno);
            return 1;
        }
        printf("shared_region_check_np call=%u ms=%.3f\n", i + 1, elapsed);
        fflush(stdout);
    }
    return 0;
}
