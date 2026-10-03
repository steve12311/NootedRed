// 仅执行用户态 mock；不调用 IOKit，不向 GPU 提交命令。
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>

extern "C"
{
    bool      wait5000ChannelStock(void*, unsigned);
    bool      wait5000EngineStock(void*, unsigned);
    bool      wait6000ChannelStock(void*, unsigned);
    bool      wait6000EngineStock(void*, unsigned);
    bool      wait6000ChannelPatched(void*, unsigned);
    bool      wait6000EnginePatched(void*, unsigned);
    uint32_t  mockTraceFlag{0};
    uint32_t* mockTracePointer{&mockTraceFlag};
}

std::vector<char> events;
unsigned          idleChecks, readyAfter;
bool              timeout;
struct Hardware
{
    std::array<void*, 0x400 / 8>* table;
};
void disable(void*) { events.push_back('D'); }
void enable(void*) { events.push_back('E'); }
bool query(void*)
{
    events.push_back('Q');
    return false;
}
bool idle(void*)
{
    events.push_back('I');
    return ++idleChecks >= readyAfter;
}
extern "C" bool mockPollWait(void*)
{
    events.push_back('P');
    return !timeout;
}
extern "C" void mockTrace() { assert(false && "追踪已关闭，不应调用"); }

void check(bool (*wait)(void*, unsigned), bool channel, bool oldHardware, bool defect)
{
    std::array<void*, 0x400 / 8> hwTable{};
    if (oldHardware) {
        hwTable[0x3E0 / 8] = reinterpret_cast<void*>(query);
        hwTable[0x3E8 / 8] = reinterpret_cast<void*>(disable);
        hwTable[0x3F0 / 8] = reinterpret_cast<void*>(enable);
    }
    else {
        hwTable[0x3E0 / 8] = reinterpret_cast<void*>(disable);
        hwTable[0x3E8 / 8] = reinterpret_cast<void*>(enable);
    }
    Hardware                     hardware{&hwTable};
    std::array<void*, 0x200 / 8> table{};
    table[(channel ? 0x168 : 0x178) / 8] = reinterpret_cast<void*>(idle);
    std::array<uint8_t, 0x40> object{};
    auto*                     tableAddress    = table.data();
    auto*                     hardwareAddress = &hardware;
    std::memcpy(object.data(), &tableAddress, sizeof(tableAddress));
    std::memcpy(object.data() + (channel ? 0x20 : 0x18), &hardwareAddress, sizeof(hardwareAddress));
    for (unsigned scenario = 0; scenario < 3; ++scenario) {
        events.clear();
        idleChecks = 0;
        readyAfter = scenario == 0 ? 1 : 2;
        timeout    = scenario == 2;
        assert(wait(object.data(), 100) == !timeout);
        std::vector<char> expected = scenario == 0 ? std::vector<char>{'D', 'I', 'E'} :
                                     scenario == 1 ? std::vector<char>{'D', 'I', 'P', 'I', 'E'} :
                                                     std::vector<char>{'D', 'I', 'P', 'E'};
        if (defect) {
            expected.front() = 'Q';
            expected.back()  = 'D';
        }
        assert(events == expected);
    }
}

int main()
{
    check(wait5000ChannelStock, true, true, false);
    check(wait5000EngineStock, false, true, false);
    check(wait6000ChannelStock, true, false, false);
    check(wait6000EngineStock, false, false, false);
    // 先明确复现 v1.2 的错误调用序列，再要求当前生成补丁恢复原生配对语义。
    check(wait6000ChannelStock, true, true, true);
    check(wait6000EngineStock, false, true, true);
    check(wait6000ChannelPatched, true, true, false);
    check(wait6000EnginePatched, false, true, false);
}
