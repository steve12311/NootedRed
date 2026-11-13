// 共享缓存补丁的进程范围：只跳过确定不会加载 GPU 用户态驱动的系统命令行工具。
// 纯解析逻辑不依赖内核头，可在主机上用真实可执行文件验证；内核侧读取与 Hook 接线在 UserProcessFilter.cpp。
//
// 规则（任何不确定都回到“补丁”）：
//   1. 可执行文件路径位于受 SIP 保护的系统命令目录或 Xcode/CLT 工具链目录；
//   2. x86_64 切片是 MH_EXECUTE，加载命令完整且可解析；
//   3. 每个直接依赖都在已知不加载 Metal/GL/GVA 的库集合中（libSystem、Foundation 等）。
//      @rpath / @executable_path、私有框架、第三方库、任何含 GPU/媒体关键词的库都视为可能使用 GPU。
// 补丁只改写共享缓存中 Metal / GL / AppleGVA 驱动代码；进程从不加载这些映像时，跳过不会改变其行为。
#pragma once
#include <IOKit/IOTypes.h>

class KernelPatcher;

namespace UserProcessFilter
{

    // 内核侧：解析 proc_getexecutablevnode 并向协调器注册过滤器；-NRedPatchAllProcs 关闭过滤。
    void init(KernelPatcher& patcher);

    enum class Verdict : UInt8
    {
        Patch = 0,          // 路径不在范围内，按原逻辑补丁
        Skip,               // 已证明不使用 GPU 用户态驱动
        PatchFormat,        // 可执行文件无法完整解析
        PatchDependency,    // 存在可能使用 GPU 的依赖
    };

    constexpr UInt32 MaxCommandBytes = 32 * 1024;
    constexpr UInt32 HeaderBytes     = 4096;

    namespace Detail
    {
        constexpr UInt32 FatMagic      = 0xCAFEBABE;
        constexpr UInt32 FatMagic64    = 0xCAFEBABF;
        constexpr UInt32 MhMagic64     = 0xFEEDFACF;
        constexpr UInt32 CpuTypeX86_64 = 0x01000007;
        constexpr UInt32 MhExecute     = 0x2;

        constexpr UInt32 LcLoadDylib       = 0xC;
        constexpr UInt32 LcLoadWeakDylib   = 0x80000018;
        constexpr UInt32 LcReexportDylib   = 0x8000001F;
        constexpr UInt32 LcLazyLoadDylib   = 0x20;
        constexpr UInt32 LcLoadUpwardDylib = 0x80000023;

        inline UInt32 load32(const UInt8* p)
        {
            return static_cast<UInt32>(p[0]) | (static_cast<UInt32>(p[1]) << 8) | (static_cast<UInt32>(p[2]) << 16)
                   | (static_cast<UInt32>(p[3]) << 24);
        }

        inline UInt32 loadBig32(const UInt8* p)
        {
            return (static_cast<UInt32>(p[0]) << 24) | (static_cast<UInt32>(p[1]) << 16)
                   | (static_cast<UInt32>(p[2]) << 8) | static_cast<UInt32>(p[3]);
        }

        inline UInt64 loadBig64(const UInt8* p)
        {
            return (static_cast<UInt64>(loadBig32(p)) << 32) | static_cast<UInt64>(loadBig32(p + 4));
        }

        inline UInt32 length(const char* s)
        {
            UInt32 n = 0;
            while (s[n] != '\0') { ++n; }
            return n;
        }

        inline bool startsWith(const char* s, const UInt32 len, const char* prefix)
        {
            const auto n = length(prefix);
            if (len < n) { return false; }
            for (UInt32 i = 0; i < n; ++i) {
                if (s[i] != prefix[i]) { return false; }
            }
            return true;
        }

        inline bool contains(const char* s, const UInt32 len, const char* token)
        {
            const auto n = length(token);
            if (n == 0 || len < n) { return false; }
            for (UInt32 i = 0; i + n <= len; ++i) {
                UInt32 j = 0;
                while (j < n && s[i + j] == token[j]) { ++j; }
                if (j == n) { return true; }
            }
            return false;
        }

        inline bool equals(const char* s, const UInt32 len, const char* other)
        {
            return length(other) == len && startsWith(s, len, other);
        }
    }    // namespace Detail

    // 受 SIP 保护的系统命令目录，以及 Xcode / Command Line Tools 工具链（构建时大量启动的进程）。
    inline bool eligiblePath(const char* path, const UInt32 len)
    {
        if (path == nullptr || len == 0) { return false; }
        constexpr const char* Prefixes[] = {
            "/bin/",
            "/sbin/",
            "/usr/bin/",
            "/usr/sbin/",
            "/usr/libexec/",
            "/Applications/Xcode.app/Contents/Developer/Toolchains/",
            "/Applications/Xcode.app/Contents/Developer/usr/bin/",
            "/Applications/Xcode.app/Contents/Developer/usr/libexec/",
            "/System/Volumes/Data/Applications/Xcode.app/Contents/Developer/Toolchains/",
            "/System/Volumes/Data/Applications/Xcode.app/Contents/Developer/usr/bin/",
            "/System/Volumes/Data/Applications/Xcode.app/Contents/Developer/usr/libexec/",
            "/Library/Developer/CommandLineTools/usr/bin/",
            "/Library/Developer/CommandLineTools/usr/libexec/",
            "/System/Volumes/Data/Library/Developer/CommandLineTools/usr/bin/",
            "/System/Volumes/Data/Library/Developer/CommandLineTools/usr/libexec/",
        };
        for (const auto prefix : Prefixes) {
            if (Detail::startsWith(path, len, prefix)) { return true; }
        }
        return false;
    }

    // 已知不会加载 Metal / GL / AppleGVA 用户态驱动的依赖。
    inline bool safeDylib(const char* name, const UInt32 len)
    {
        if (name == nullptr || len == 0) { return false; }
        if (Detail::startsWith(name, len, "/usr/lib/")) {
            // /usr/lib 下的底层库（含 /usr/lib/swift 覆盖层）；名称含图形/媒体/ML 关键词的一律视为不安全。
            constexpr const char* GpuTokens[] = {
                "Metal", "GL",     "OpenCL", "Image",   "Graphics", "Quartz", "AppKit",  "UIKit",
                "SwiftUI", "AVF",  "AVKit",  "Media",   "Video",    "Vision", "Scene",   "Sprite",
                "Reality", "Model", "ML",    "Espresso", "ANE",     "Speech", "Natural", "Camera",
                "Display", "Quick", "Web",   "Map",     "Photo",    "Screen", "Surface", "MPS",
                "Game",    "ARKit", "Avatar", "Animation", "Sky",   "Window", "Render",  "Shader",
                "GPU",     "Accel", "Compute", "Visual",
            };
            for (const auto token : GpuTokens) {
                if (Detail::contains(name, len, token)) { return false; }
            }
            return true;
        }
        // 少量只做 XPC 遥测 / 钥匙包的私有框架；SoftLinking 等动态加载框架绝不放行。
        constexpr const char* SafePrivate[] = {
            "/System/Library/PrivateFrameworks/CoreAnalytics.framework/",
            "/System/Library/PrivateFrameworks/MobileKeyBag.framework/",
        };
        for (const auto prefix : SafePrivate) {
            if (Detail::startsWith(name, len, prefix)) { return true; }
        }
        constexpr const char* FrameworkRoot = "/System/Library/Frameworks/";
        if (!Detail::startsWith(name, len, FrameworkRoot)) { return false; }
        // 只允许明确的非图形公共框架；私有框架一律视为可能使用 GPU。
        constexpr const char* SafeFrameworks[] = {
            "Foundation",         "CoreFoundation",    "Security",         "IOKit",
            "SystemConfiguration", "CFNetwork",        "DiskArbitration",  "ServiceManagement",
            "OpenDirectory",      "Network",           "Kerberos",         "GSS",
            "CoreServices",       "CryptoTokenKit",    "LocalAuthentication", "EndpointSecurity",
            "NetworkExtension",   "CoreWLAN",          "Combine",          "OSLog",
            "Collaboration",      "DirectoryService",  "PCSC",             "SecurityFoundation",
            "Accelerate",         "System",            "LDAP",             "ExceptionHandling",
            "CryptoKit",
        };
        const auto rootLen = Detail::length(FrameworkRoot);
        const char* rest   = name + rootLen;
        const auto  restLen = len - rootLen;
        UInt32      nameLen = 0;
        while (nameLen < restLen && rest[nameLen] != '.' && rest[nameLen] != '/') { ++nameLen; }
        if (nameLen == 0 || !Detail::startsWith(rest + nameLen, restLen - nameLen, ".framework/")) { return false; }
        for (const auto framework : SafeFrameworks) {
            if (Detail::equals(rest, nameLen, framework)) { return true; }
        }
        return false;
    }

    // 定位 x86_64 切片中的 mach_header_64；header 为文件开头至多 HeaderBytes 字节。
    inline bool findSlice(const UInt8* header, const UInt32 headerSize, UInt64& sliceOffset)
    {
        using namespace Detail;
        if (header == nullptr || headerSize < 8) { return false; }
        const auto magic = loadBig32(header);
        if (magic != FatMagic && magic != FatMagic64) {
            sliceOffset = 0;
            return load32(header) == MhMagic64;
        }
        const auto count = loadBig32(header + 4);
        const UInt32 entry = magic == FatMagic64 ? 32 : 20;
        if (count == 0 || count > 32 || 8 + count * entry > headerSize) { return false; }
        for (UInt32 i = 0; i < count; ++i) {
            const auto* arch = header + 8 + i * entry;
            if (loadBig32(arch) != CpuTypeX86_64) { continue; }
            sliceOffset = magic == FatMagic64 ? loadBig64(arch + 8) : loadBig32(arch + 8);
            return sliceOffset != 0;
        }
        return false;
    }

    // 校验 x86_64 MH_EXECUTE 头，返回加载命令数量与字节数。
    inline bool parseHeader(const UInt8* mh, const UInt32 size, UInt32& ncmds, UInt32& sizeofcmds)
    {
        using namespace Detail;
        if (mh == nullptr || size < 32 || load32(mh) != MhMagic64 || load32(mh + 4) != CpuTypeX86_64
            || load32(mh + 12) != MhExecute)
        {
            return false;
        }
        ncmds      = load32(mh + 16);
        sizeofcmds = load32(mh + 20);
        return ncmds != 0 && ncmds <= 4096 && sizeofcmds >= 8 && sizeofcmds <= MaxCommandBytes;
    }

    // 遍历加载命令：只有全部直接依赖都安全且至少有一个依赖时才返回 Skip。
    inline Verdict classifyCommands(const UInt8* cmds, const UInt32 sizeofcmds, const UInt32 ncmds)
    {
        using namespace Detail;
        if (cmds == nullptr || sizeofcmds > MaxCommandBytes) { return Verdict::PatchFormat; }
        UInt32 offset = 0, dylibs = 0;
        for (UInt32 i = 0; i < ncmds; ++i) {
            if (offset > sizeofcmds || sizeofcmds - offset < 8) { return Verdict::PatchFormat; }
            const auto* lc      = cmds + offset;
            const auto  cmd     = load32(lc);
            const auto  cmdsize = load32(lc + 4);
            if (cmdsize < 8 || cmdsize > sizeofcmds - offset) { return Verdict::PatchFormat; }
            if (cmd == LcLoadDylib || cmd == LcLoadWeakDylib || cmd == LcReexportDylib || cmd == LcLazyLoadDylib
                || cmd == LcLoadUpwardDylib)
            {
                if (cmdsize < 24) { return Verdict::PatchFormat; }
                const auto nameOffset = load32(lc + 8);
                if (nameOffset < 24 || nameOffset >= cmdsize) { return Verdict::PatchFormat; }
                const auto* name = reinterpret_cast<const char*>(lc + nameOffset);
                const auto  max  = cmdsize - nameOffset;
                UInt32      len  = 0;
                while (len < max && name[len] != '\0') { ++len; }
                if (len == max || len == 0) { return Verdict::PatchFormat; }
                if (!safeDylib(name, len)) { return Verdict::PatchDependency; }
                ++dylibs;
            }
            offset += cmdsize;
        }
        return dylibs != 0 ? Verdict::Skip : Verdict::PatchFormat;
    }

    // 读取接口：从可执行文件 offset 处最多读取 size 字节，返回实际读取的字节数（错误返回 0）。
    using Reader = UInt32 (*)(void* context, UInt64 offset, void* out, UInt32 size);

    // 完整判定。header 与 commands 由调用方提供（内核侧避免大块栈缓冲区）。
    inline Verdict classify(const char* path, const UInt32 pathLen, const Reader read, void* context, UInt8* header,
                            UInt8* commands)
    {
        if (!eligiblePath(path, pathLen)) { return Verdict::Patch; }
        if (read == nullptr || header == nullptr || commands == nullptr) { return Verdict::PatchFormat; }
        const auto headerSize = read(context, 0, header, HeaderBytes);
        UInt64     slice{0};
        if (headerSize < 32 || !findSlice(header, headerSize, slice)) { return Verdict::PatchFormat; }
        UInt8 mh[32];
        if (read(context, slice, mh, sizeof(mh)) != sizeof(mh)) { return Verdict::PatchFormat; }
        UInt32 ncmds{0}, sizeofcmds{0};
        if (!parseHeader(mh, sizeof(mh), ncmds, sizeofcmds)) { return Verdict::PatchFormat; }
        if (read(context, slice + sizeof(mh), commands, sizeofcmds) != sizeofcmds) { return Verdict::PatchFormat; }
        return classifyCommands(commands, sizeofcmds, ncmds);
    }

}    // namespace UserProcessFilter
