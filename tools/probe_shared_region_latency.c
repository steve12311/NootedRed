// 只检查当前进程的共享缓存映射，不创建 GPU 设备或提交命令。
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define MAX_SAMPLES 4096

static double seconds(void)
{
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return value.tv_sec + value.tv_nsec / 1000000000.0;
}

static int compare(const void* left, const void* right)
{
    const double a = *(const double*)left, b = *(const double*)right;
    return a < b ? -1 : (a > b ? 1 : 0);
}

// 最近秩百分位；样本不足时返回最后一个。
static double percentile(const double* samples, unsigned count, double fraction)
{
    unsigned index = (unsigned)(fraction * count);
    if (index >= count) { index = count - 1; }
    return samples[index];
}

int main(int argc, char** argv)
{
    unsigned iterations = argc > 1 ? (unsigned)strtoul(argv[1], NULL, 0) : 64;
    if (iterations == 0 || iterations > MAX_SAMPLES) { iterations = MAX_SAMPLES; }
    double samples[MAX_SAMPLES];
    for (unsigned i = 0; i < iterations; ++i) {
        uint64_t base    = 0;
        double   start   = seconds();
        int      result  = syscall(SYS_shared_region_check_np, &base);
        double   elapsed = (seconds() - start) * 1000.0;
        if (result != 0 || base == 0) {
            fprintf(stderr, "shared_region_check_np failed: result=%d errno=%d\n", result, errno);
            return 1;
        }
        samples[i] = elapsed;
        // 只逐条打印前八次，便于对照原生调用与 Hook 后的首次路径。
        if (i < 8) {
            printf("shared_region_check_np call=%u ms=%.3f\n", i + 1, elapsed);
            fflush(stdout);
        }
    }
    qsort(samples, iterations, sizeof(samples[0]), compare);
    printf("samples=%u min=%.3f p50=%.3f p95=%.3f p99=%.3f max=%.3f ms\n", iterations, samples[0],
           percentile(samples, iterations, 0.50), percentile(samples, iterations, 0.95),
           percentile(samples, iterations, 0.99), samples[iterations - 1]);
    return 0;
}
