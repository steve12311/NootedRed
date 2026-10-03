// 原生选择代码的离线边界 mock：不调用 IOKit、VideoToolbox 或解码硬件。
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

struct Renderer
{
    Renderer*                  next{};
    std::array<uint8_t, 0x420> unused{};
    uint32_t                   rendererID{};
    std::array<uint8_t, 0x2C>  padding{};
    uint64_t                   entryID{};
    uint8_t                    online{1}, removable{0}, secondary{0}, active{1};
};
static_assert(offsetof(Renderer, rendererID) == 0x428);
static_assert(offsetof(Renderer, entryID) == 0x458);
static_assert(offsetof(Renderer, active) == 0x463);
struct Output
{
    Renderer** begin;
    Renderer** end;
    Renderer** capacity;
};
using Choice                 = void (*)(unsigned, uint64_t, unsigned, Output*);
const char*         board    = "Mac-5F9802EFE386AA28";
bool                forceAMD = false;
extern "C"
{
    uintptr_t  guardValue   = 0x1234;
    uintptr_t* guardPointer = &guardValue;
    Renderer*  gpuHead      = nullptr;
    void       stockChoice(unsigned, uint64_t, unsigned, Output*);
    void       candidateChoice(unsigned, uint64_t, unsigned, Output*);
    bool       mockPreference(const char*) { return forceAMD; }
    void       mockBoardID(char** out, unsigned* length)
    {
        *out    = board ? strdup(board) : nullptr;
        *length = board ? static_cast<unsigned>(strlen(board)) : 0;
    }
    void mockPush(Output* output, Renderer* const* renderer)
    {
        assert(output->end < output->capacity && *renderer);
        *output->end++ = *renderer;
    }
    void mockDefaultGPU(uint64_t* out) { *out = gpuHead ? gpuHead->entryID : 0; }
    void mockLog(unsigned, const char*, ...) { }
    [[noreturn]]
    void mockFailure()
    {
        abort();
    }
}
std::vector<Renderer*> run(Choice function, unsigned flow, uint64_t entry = 0, unsigned force = 0)
{
    std::array<Renderer*, 32> storage{};
    Output                    output{storage.data(), storage.data(), storage.data() + storage.size()};
    function(flow, entry, force, &output);
    return {output.begin, output.end};
}
int main()
{
    Renderer amd{}, intel{}, unsupported{};
    amd.rendererID         = 0x1020004;
    amd.entryID            = 1;
    intel.rendererID       = 0x1080001;
    intel.entryID          = 2;
    unsupported.rendererID = 0x123456;
    unsupported.entryID    = 3;
    gpuHead                = &amd;
    // 明确复现 MacBookPro16,2 的 AMD-only 选择为空，再要求生成补丁修正。
    assert(run(stockChoice, 2).empty() && run(stockChoice, 14).empty());
    if (run(candidateChoice, 2) != std::vector<Renderer*>{&amd}
        || run(candidateChoice, 14) != std::vector<Renderer*>{&amd})
    {
        fprintf(stderr, "FAIL: 原生机型选择遗漏 AMD-only 解码器\n");
        return 1;
    }
    unsigned cases = 0;
    for (const char* model : {"Mac-5F9802EFE386AA28", "Mac-7BA5B2D9E42DDD94", "Mac-F60DEB81FF30ACF6",
                              "Mac-27AD2F918AE68F61", static_cast<const char*>(nullptr)})
    {
        board = model;
        for (unsigned topology = 0; topology < 6; ++topology) {
            amd.next = intel.next = unsupported.next = nullptr;
            gpuHead                                  = topology == 0 ? nullptr :
                                                       topology == 1 ? &amd :
                                                       topology == 2 ? &intel :
                                                       topology == 5 ? &unsupported :
                                                                       &amd;
            if (topology == 3) { amd.next = &intel; }
            if (topology == 4) { amd.next = &unsupported; }
            for (bool forced : {false, true}) {
                forceAMD = forced;
                for (unsigned flow : {2, 11, 14}) {
                    auto old = run(stockChoice, flow), fixed = run(candidateChoice, flow);
                    bool fallback = model && strcmp(model, "Mac-5F9802EFE386AA28") == 0
                                    && (topology == 1 || topology == 4) && !forced;
                    assert(fixed == (fallback ? std::vector<Renderer*>{&amd} : old));
                    ++cases;
                    assert(run(candidateChoice, flow, 1, 1) == run(stockChoice, flow, 1, 1));
                    ++cases;
                }
            }
        }
    }
    board    = "Mac-5F9802EFE386AA28";
    forceAMD = false;
    gpuHead  = &amd;
    amd.next = nullptr;
    for (unsigned flag = 0; flag < 2; ++flag) {
        amd.active    = flag == 0 ? 0 : 1;
        amd.removable = flag == 1 ? 1 : 0;
        assert(run(candidateChoice, 2) == run(stockChoice, 2));
    }
    amd.active    = 1;
    amd.removable = 0;
    amd.online    = 0;
    assert(run(candidateChoice, 2, 1, 1) == run(stockChoice, 2, 1, 1));
    printf(
        "PASS: AMD-only fallback; %u native selection cases, explicit requests and ineligible GPU refusal preserved\n",
        cases);
}
