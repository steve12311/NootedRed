// 仅对明确列出的基础系统命令跳过共享缓存补丁；加载命令检查提供额外约束。
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
        Skip,               // 白名单基础命令且依赖符合约束
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

        inline bool equals(const char* s, const UInt32 len, const char* other)
        {
            return length(other) == len && startsWith(s, len, other);
        }
    }    // namespace Detail

    inline bool eligiblePath(const char* path, const UInt32 len)
    {
        if (path == nullptr || len == 0) { return false; }
        constexpr const char* Paths[] = {
            "/bin/cat",          "/bin/chmod",        "/bin/cp",           "/bin/date",         "/bin/dd",
            "/bin/df",           "/bin/echo",         "/bin/expr",         "/bin/hostname",     "/bin/kill",
            "/bin/link",         "/bin/ln",           "/bin/ls",           "/bin/mkdir",        "/bin/mv",
            "/bin/pwd",          "/bin/realpath",     "/bin/rm",           "/bin/rmdir",        "/bin/sleep",
            "/bin/sync",         "/bin/test",         "/bin/unlink",       "/usr/bin/arch",     "/usr/bin/awk",
            "/usr/bin/base64",   "/usr/bin/basename", "/usr/bin/bunzip2",  "/usr/bin/bzip2",    "/usr/bin/cksum",
            "/usr/bin/cmp",      "/usr/bin/col",      "/usr/bin/colrm",    "/usr/bin/column",   "/usr/bin/comm",
            "/usr/bin/compress", "/usr/bin/cut",      "/usr/bin/diff",     "/usr/bin/dirname",  "/usr/bin/du",
            "/usr/bin/env",      "/usr/bin/expand",   "/usr/bin/false",    "/usr/bin/find",     "/usr/bin/fold",
            "/usr/bin/grep",     "/usr/bin/gunzip",   "/usr/bin/gzip",     "/usr/bin/head",     "/usr/bin/id",
            "/usr/bin/join",     "/usr/bin/logname",  "/usr/bin/mktemp",   "/usr/bin/nl",       "/usr/bin/od",
            "/usr/bin/paste",    "/usr/bin/printenv", "/usr/bin/printf",   "/usr/bin/readlink", "/usr/bin/rev",
            "/usr/bin/sed",      "/usr/bin/seq",      "/usr/bin/sort",     "/usr/bin/split",    "/usr/bin/stat",
            "/usr/bin/sum",      "/usr/bin/tail",     "/usr/bin/tar",      "/usr/bin/tee",      "/usr/bin/touch",
            "/usr/bin/tr",       "/usr/bin/true",     "/usr/bin/truncate", "/usr/bin/tsort",    "/usr/bin/uname",
            "/usr/bin/unexpand", "/usr/bin/uniq",     "/usr/bin/wc",       "/usr/bin/whoami",   "/usr/bin/xargs",
            "/usr/bin/yes",
        };
        for (const auto candidate : Paths) {
            if (Detail::equals(path, len, candidate)) { return true; }
        }
        return false;
    }

    inline bool safeDylib(const char* name, const UInt32 len)
    {
        if (name == nullptr || len == 0) { return false; }
        constexpr const char* Libraries[] = {
            "/usr/lib/libSystem.B.dylib", "/usr/lib/libutil.dylib",      "/usr/lib/libncurses.5.4.dylib",
            "/usr/lib/libbz2.1.0.dylib",  "/usr/lib/liblzma.5.dylib",    "/usr/lib/libz.1.dylib",
            "/usr/lib/libiconv.2.dylib",  "/usr/lib/libcharset.1.dylib", "/usr/lib/libarchive.2.dylib",
            "/usr/lib/libpcre.0.dylib",
        };
        for (const auto library : Libraries) {
            if (Detail::equals(name, len, library)) { return true; }
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
