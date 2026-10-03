// 指定缓存的视频解码补丁，写入进程私有 COW 页。
#pragma once
#include <Headers/kern_patcher.hpp>
namespace UserVideoDecode { void init(KernelPatcher& patcher); }
