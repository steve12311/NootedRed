// 执行真实原生/候选机器码；虚表出口使用 CPU 替身，不创建 GPU 对象。
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <utility>

extern "C" void stockPitch(void*, void*, void*);
extern "C" void fixedPitch(void*, void*, void*);
extern "C" unsigned stockSize(void*);
extern "C" unsigned fixedSize(void*);
extern "C" void stockAvc(void*, void*, void*);
extern "C" void fixedAvc(void*, void*, void*);
extern "C" void stockHevc(void*, void*, void*);
extern "C" void fixedHevc(void*, void*, void*);

template<typename T, size_t N>
static void put(std::array<uint8_t, N>& bytes, size_t offset, T value)
{
    assert(offset + sizeof(value) <= N);
    memcpy(bytes.data() + offset, &value, sizeof(value));
}
static std::array<uint32_t, 11> parameters;
static void fill(void*, void*, uint32_t* p) { p[0] = 2; p[9] = 7; p[10] = 8; }
static void capture(void*, unsigned kind, const void* p)
{
    assert(kind == 10);
    memcpy(parameters.data(), p, sizeof(parameters));
}

int main()
{
    std::array<uint8_t, 0x500> encoder{};
    std::array<uint8_t, 0x88> texture{};
    std::array<uintptr_t, 24> processorTable{}, commandTable{};
    processorTable[0xb0 / 8] = reinterpret_cast<uintptr_t>(fill);
    commandTable[0x70 / 8] = reinterpret_cast<uintptr_t>(capture);
    uintptr_t processor = reinterpret_cast<uintptr_t>(processorTable.data());
    uintptr_t command = reinterpret_cast<uintptr_t>(commandTable.data());
    put(encoder, 0x2f8, &processor);
    put(encoder, 0x458, &command);
    put(encoder, 0x210, uint32_t{0x151800});
    unsigned cases = 0;
    for (uint32_t width : {16, 640, 1280, 1920, 3840}) {
        for (uint32_t element : {1, 2, 4}) {
            for (uint32_t alignment : {256, 2048}) {
                uint32_t stride = (width * element + alignment - 1) & ~(alignment - 1);
                put(encoder, 0x424, width);
                put(texture, 0x80, stride);
                put(texture, 0x84, element);
                stockPitch(encoder.data(), nullptr, texture.data());
                auto stock = parameters;
                assert(stock[6] == ((width * element + 255) & ~255U) / element);
                fixedPitch(encoder.data(), nullptr, texture.data());
                assert(parameters[6] == stride / element && parameters[7] == stride / element / 2);
                for (unsigned i = 0; i < 11; ++i)
                    if (i != 6 && i != 7) assert(stock[i] == parameters[i]);
                ++cases;
            }
        }
    }
    assert(stockSize(nullptr) == 164 && fixedSize(nullptr) == 48);
    std::array<uint8_t, 164 * 4> feedback{};
    std::array<uint8_t, 32> info{}, job{};
    std::array<uint8_t, 40> context{};
    std::array<uint8_t, 0x338> buffers{};
    std::array<uint8_t, 0x120> frame{};
    put(info, 8, job.data());
    put(job, 0x10, context.data());
    put(context, 0x20, buffers.data());
    put(buffers, 0x330, frame.data());
    for (unsigned index = 0; index < 3; ++index) {
        put(feedback, index * 48 + 0x10, uint32_t{1});
        put(job, 8, index);
        for (auto function : {stockAvc, stockHevc}) {
            function(nullptr, info.data(), feedback.data());
            uint32_t status;
            memcpy(&status, info.data(), sizeof(status));
            assert(status == (index == 0 ? 0U : 4U));
        }
        for (auto function : {fixedAvc, fixedHevc}) {
            for (auto [firmware, expected] : {std::pair{0U, 0U}, std::pair{0x10000001U, 1U}, std::pair{7U, 8U}}) {
                put(feedback, index * 48 + 0xc, firmware);
                function(nullptr, info.data(), feedback.data());
                uint32_t status;
                memcpy(&status, info.data(), sizeof(status));
                assert(status == expected);
                ++cases;
            }
        }
        put(feedback, index * 48 + 0xc, uint32_t{0});
    }
    printf("PASS %u native CPU cases: actual source pitch, untouched parameters, feedback slots and errors\n", cases);
}
