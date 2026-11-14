/* Source-only compatibility for the Sequoia AMD X5000 descriptor ABI. */
#ifndef NRED_METAL_DESCRIPTOR_ABI_H
#define NRED_METAL_DESCRIPTOR_ABI_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define NRMC_PIPELINE_PRIVATE_SIZE 632u
#define NRMC_PIPELINE_FLAGS_OFFSET 224u
#define NRMC_COLOR_PRIVATE_SIZE 8u

static inline uint64_t nrMCField(uint64_t word, unsigned shift, unsigned width) {
    return (word >> shift) & ((UINT64_C(1) << width) - 1);
}

/* Modern blending has a two-bit state: its public getter is true only for 1.
 * Bit 36 of the modern write mask and new-only flags have no legacy equivalent.
 */
static inline uint64_t nrMCColorToLegacy(uint64_t word) {
    return (nrMCField(word, 0, 2) == 1) | (nrMCField(word, 2, 26) << 1) | (nrMCField(word, 32, 4) << 27) |
           (nrMCField(word, 42, 22) << 36);
}

static inline uint64_t nrMCPipelineToLegacy(uint64_t word) {
    return (nrMCField(word, 0, 2) != 0) | ((uint64_t)(nrMCField(word, 2, 2) != 0) << 1) |
           (nrMCField(word, 4, 25) << 2) | (nrMCField(word, 32, 9) << 27);
}

/* A profile describes an interface, not a PCI ID, SMBIOS or machine UUID.
 * New layouts must be measured before adding another profile.
 */
static inline bool nrMCFrameworkProfile(unsigned darwinMajor, size_t pipelineSize, size_t colorSize,
                                        uint64_t colorSample, uint64_t rasterDisabled,
                                        uint64_t rasterEnabled) {
    return darwinMajor == 27 && pipelineSize == 736 && colorSize == 8 &&
           colorSample == UINT64_C(0x1180f00002100) && !(rasterDisabled & 0x10) &&
           (rasterEnabled ^ rasterDisabled) == 0x10;
}

static inline bool nrMCDonorProfile(uint32_t cpuType, uint32_t platform, uint32_t minOS,
                                    const char *installName) {
    return cpuType == UINT32_C(0x01000007) && platform == 1 && (minOS >> 16) == 15 && installName &&
           !strcmp(installName, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/"
                                "AMDRadeonX5000MTLDriver");
}
#endif
