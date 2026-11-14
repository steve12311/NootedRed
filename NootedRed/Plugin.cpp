// Plugin Entry Point and Configuration
//
// Copyright © 2022-2025 ChefKiss. Licensed under the Thou Shalt Not Profit License version 1.5.
// See LICENSE for details.

#include <Headers/kern_api.hpp>
#include <Headers/kern_util.hpp>
#include <Headers/plugin_start.hpp>
#include <NRed.hpp>

static const char* bootargOff   = "-NRedOff";
static const char* bootargDebug = "-NRedDebug";
static const char* bootargBeta  = "-NRedBeta";

PluginConfiguration ADDPR(config){
    xStringify(PRODUCT_NAME),
    parseModuleVersion(xStringify(MODULE_VERSION)),
    LiluAPI::AllowNormal | LiluAPI::AllowInstallerRecovery | LiluAPI::AllowSafeMode,
    &bootargOff,
    1,
    &bootargDebug,
    1,
    &bootargBeta,
    1,
    KernelVersion::Catalina,
    static_cast<KernelVersion>(27),
    []() {
        if (version_major >= 27 && (version_major != 27 || version_minor != 2
            || !checkKernelArgument("-NRedDonor24"))) {
            SYSLOG("DonorProfile", "Darwin 27 requires the explicit 27.2 Sequoia donor profile");
            return;
        }
        NRed::singleton().init();
    },
};
