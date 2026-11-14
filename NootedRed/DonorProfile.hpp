// Optional Sequoia donor profile: the kernel version stays real; only old AMD binary
// layout selection uses the pinned donor version. UUIDs checked before patching.
#pragma once
#include <PenguinWizardry/KernelVersion.hpp>
#include <Headers/kern_util.hpp>
#include <mach-o/loader.h>
inline bool usingSequoiaDonor() { return version_major == 27 && version_minor == 2 && checkKernelArgument("-NRedDonor24"); }
inline const PenguinWizardry::KernelVersion& appleDriverVersion() {
    static const PenguinWizardry::KernelVersion donor{24,6};
    return usingSequoiaDonor() ? donor : currentKernelVersion();
}
inline void requireDonorUUID(mach_vm_address_t start, size_t length, const UInt8 *expected) {
    if (!usingSequoiaDonor()) return;
    auto h = reinterpret_cast<const mach_header_64 *>(start);
    PANIC_COND(length < sizeof(*h) || h->magic != MH_MAGIC_64 || h->sizeofcmds > length-sizeof(*h),
               "DonorProfile", "Invalid donor Mach-O header");
    auto p = reinterpret_cast<const UInt8 *>(h+1);
    const auto end = p+h->sizeofcmds;
    for (UInt32 i=0;i<h->ncmds;i++) {
        PANIC_COND(p+sizeof(load_command)>end,"DonorProfile","Truncated donor commands");
        auto c=reinterpret_cast<const load_command *>(p);
        PANIC_COND(c->cmdsize<sizeof(*c) || c->cmdsize>static_cast<size_t>(end-p),"DonorProfile","Invalid donor command");
        if(c->cmd==LC_UUID) {
            PANIC_COND(c->cmdsize<sizeof(uuid_command) || memcmp(p+8,expected,16),
                       "DonorProfile","Pinned 24H32 driver UUID mismatch; refusing offsets");
            SYSLOG("DonorProfile","Verified pinned 24H32 driver UUID");
            return;
        }
        p+=c->cmdsize;
    }
    PANIC("DonorProfile","Donor UUID absent");
}
