// 指定缓存的视频解码补丁，写入进程私有 COW 页。
#pragma once
#include <Headers/kern_patcher.hpp>
namespace UserVideoDecode
{
    void init(KernelPatcher& patcher);
    // VP9 (-NRedVP9Trace): il backend VP9 segnala i suoi passi con shared_region_check_np e un argomento marcato
    // (bit 63..48 = 0x5639). Restituisce true se l'argomento è una traccia: il chiamante risponde EINVAL senza
    // chiamare la funzione originale.
    bool traceVP9(void* process, UInt64 startAddress);
}    // namespace UserVideoDecode
