// 直接执行原生与候选机器码；外部调用仅使用 CPU 替身，不创建 GPU 对象。
#include <array>
#include <cassert>
#include <csetjmp>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>

extern "C" unsigned stockCapability(void*, const void*);
extern "C" unsigned fixedCapability(void*, const void*);
extern "C" unsigned stockAvc(void*, void*, void*, void*, unsigned, unsigned);
extern "C" unsigned fixedAvc(void*, void*, void*, void*, unsigned, unsigned);
extern "C" unsigned nativeProcess(void*,void*,void*,void*,void*,void*);
static unsigned prepared,submitted;
extern "C" unsigned prepareDecode(){prepared++;return 0;}
extern "C" unsigned submitDecode(){submitted++;return 0;}
static unsigned availability;
static std::jmp_buf accepted;
extern "C" unsigned available(void*) { return availability; }
extern "C" void acceptAvc() { std::longjmp(accepted, 1); }

template<size_t N, typename T> static void put(std::array<uint8_t, N>& data, size_t offset, T value)
{
    assert(offset + sizeof(value) <= N);
    memcpy(data.data() + offset, &value, sizeof(value));
}
extern "C" unsigned stockFrame(void*,void*,void*,void*);
extern "C" unsigned fixedFrame(void*,void*,void*,void*);
extern "C" unsigned stockConfigure(void*,unsigned,void*);
extern "C" unsigned fixedConfigure(void*,unsigned,void*);
extern "C" { uintptr_t vcn2vtable[32]{}; }
static unsigned checks;
#include "payload.hpp"
int main()
{
    preparePayload();
    std::array<uint8_t, 0xb400> caps{};
    for (unsigned offset : {0xb310,0xb314,0xb320,0xb324,0xb328,0xb334,0xb338,0xb350,0xb354,0xb358,0xb35c})
        put(caps, offset, uint32_t{4096});
    for (unsigned offset : {0xb354,0xb35c,0xb314}) put(caps, offset, uint32_t{2304});
    for (unsigned offset : {0xb308,0xb30c,0xb318,0xb31c,0xb32c,0xb330}) put(caps, offset, uint32_t{64});
    for (unsigned offset : {0xb300,0xb304}) put(caps, offset, uint32_t{4096*2304});
    std::array<uint8_t,8> hardware{};
    put(hardware,4,uint16_t{0x1638});put(caps,0xb2e0,hardware.data());
    std::array<uint8_t, 48> item{};
    struct Request { uint32_t count, reserved; const void* const* items; };
    const void* ptr=item.data(); Request request{1,0,&ptr};
    put(item, 0x18, uint32_t{1});
    for (uint64_t codec : {uint64_t{0x20},uint64_t{0x800},uint64_t{0x1000}}) {
        put(item,0,uint32_t{0x20}); put(item,8,codec);
        for (uint32_t width : {0U,1U,64U,126U,127U,128U,129U,130U,131U,132U,1920U,2160U,4096U,4098U,0x80000000U,0xffffffffU})
            for (uint32_t height : {0U,1U,64U,126U,127U,128U,129U,130U,1080U,2304U,2306U,3840U,0xffffffffU}) {
                put(item,0x20,width); put(item,0x24,height);
                const unsigned stock=stockCapability(caps.data(),&request);
                const unsigned fixed=fixedCapability(caps.data(),&request);
                const bool allowed=width>=(codec==0x1000?130U:128U) && width<=4096 && height>=128 && height<=2304
                    && !(width&1) && !(height&1);
                assert(fixed==(allowed?stock:5));
                if (width==64 && height==64) assert(stock==0 && fixed==5);
                ++checks;
            }
    }
    // 解码和未知功能分支保持原生判定，包括原生面积上限。
    for (uint64_t codec : {uint64_t{8},uint64_t{0x800},uint64_t{0x1000000},uint64_t{0x40}}) {
        put(item,0,uint32_t{1});put(item,8,codec);
        for (unsigned mode : {0U,3U,5U}) for (unsigned width : {0U,64U,80U,2160U,4096U,4097U})
            for (unsigned height : {0U,64U,80U,2304U,3840U,4096U}) {
                put(item,0x10,mode);put(item,0x20,width);put(item,0x24,height);
                assert(stockCapability(caps.data(),&request)==fixedCapability(caps.data(),&request));++checks;
            }
    }
    availability=1;
    assert(stockCapability(caps.data(),&request)==1 && fixedCapability(caps.data(),&request)==1);++checks;
    availability=0;request.count=0;
    assert(stockCapability(caps.data(),&request)==0 && fixedCapability(caps.data(),&request)==0);++checks;

    std::array<uint8_t,0x600> encoder{};
    std::array<uint8_t,0x90> texture{};
    std::array<uint8_t,0x468> frameContext{};
    std::array<uint8_t,0x98> frameConfig{};
    put(frameContext,0x460,frameConfig.data());put(frameConfig,0,hardware.data());put(encoder,0x58,frameContext.data());
    put(encoder,0,vcn2vtable);vcn2vtable[0xc0/8]=reinterpret_cast<uintptr_t>(acceptAvc);
    uintptr_t configVtable[5]{}; configVtable[4]=reinterpret_cast<uintptr_t>(acceptAvc);
    auto configObject=configVtable;put(encoder,0x2f8,&configObject);
    for (unsigned codec : {0U,1U}) for (uint32_t width : {0U,64U,126U,128U,130U,1920U,4096U,4098U,0xffffffffU})
        for (uint32_t height : {0U,64U,126U,128U,1080U,2304U,2306U,3840U}) {
            const uint32_t paddedWidth=(width+127)&~127U, paddedHeight=(height+15)&~15U;
            put(encoder,0x52c,static_cast<uint8_t>(codec));put(encoder,0x60,width);put(encoder,0x64,height);
            put(encoder,0x424,paddedWidth);put(encoder,0x428,paddedHeight);
            put(texture,0x70,width);put(texture,0x74,height);put(texture,0x78,paddedWidth);put(texture,0x7c,paddedHeight);
            if (!setjmp(accepted)) { stockFrame(encoder.data(),nullptr,texture.data(),nullptr);assert(false); }
            const bool good=width>=(codec?130U:128U) && width<=4096 && height>=128 && height<=2304 && !(width&1) && !(height&1);
            bool reached=false;
            if (!setjmp(accepted)) { stockConfigure(encoder.data(),0,nullptr);assert(false); }
            if (!setjmp(accepted)) assert(fixedConfigure(encoder.data(),0,nullptr)==5);
            else reached=true;
            assert(reached==good);++checks;reached=false;
            if (!setjmp(accepted)) assert(fixedFrame(encoder.data(),nullptr,texture.data(),nullptr)==5);
            else reached=true;
            assert(reached==good);++checks;
        }
    put(encoder,0x52c,uint8_t{1});put(encoder,0x60,uint32_t{1920});put(encoder,0x64,uint32_t{1080});
    put(encoder,0x424,uint32_t{1920});put(encoder,0x428,uint32_t{1088});
    put(texture,0x70,uint32_t{1920});put(texture,0x74,uint32_t{1080});put(texture,0x78,uint32_t{2048});put(texture,0x7c,uint32_t{1088});
    assert(fixedFrame(encoder.data(),nullptr,nullptr,nullptr)==5);++checks;
    for (unsigned field : {0x70U,0x74U,0x78U,0x7cU}) {
        uint32_t original;memcpy(&original,texture.data()+field,4);
        for (uint32_t invalid : {64U,4097U,0xffffffffU}) {
            put(texture,field,invalid);
            assert(fixedFrame(encoder.data(),nullptr,texture.data(),nullptr)==5);++checks;
        }
        put(texture,field,original);
    }
    std::array<uint8_t,0x80> processor{};
    std::array<uint8_t,0x100> decoder{};
    std::array<uint8_t,0x200> context{};
    std::array<uint8_t,0x300> picture{};
    std::array<uint8_t,0x900> sps{};
    std::array<uint8_t,0xe00> message{};
    std::array<uint8_t,0x100> auxiliary{};
    put(processor,0x18,frameContext.data());put(processor,0x10,decoder.data());put(decoder,0x58,context.data());
    put(context,0xe0,message.data());put(context,0x1c0,auxiliary.data());put(picture,0x260,sps.data());
    for (unsigned profile : {0U,66U,77U,88U,100U,110U,122U,244U,0xffffffffU})
        for (unsigned chroma : {0U,1U,2U,3U}) for (unsigned plane : {0U,1U})
            for (unsigned luma : {0U,1U,2U,4U}) for (unsigned color : {0U,2U}) {
                put(sps,4,profile);put(sps,0xc,chroma);put(sps,0x10,plane);put(sps,0x14,luma);put(sps,0x18,color);
                bool reached=false;
                if (!setjmp(accepted)) { stockAvc(processor.data(),picture.data(),nullptr,nullptr,0,0);assert(false); }
                const bool supported=(profile==66||profile==77||profile==100) && chroma==1 && !plane && !luma && !color;
                if (!setjmp(accepted)) {
                    assert(fixedAvc(processor.data(),picture.data(),nullptr,nullptr,0,0)==5);
                } else reached=true;
                assert(reached==supported);++checks;
            }
    put(picture,0x260,static_cast<void*>(nullptr));
    assert(fixedAvc(processor.data(),picture.data(),nullptr,nullptr,0,0)==5);++checks;
    // 实际上游 Process 必须传播 SPS 守卫的错误，不能继续调用最后的命令处理入口。
    uintptr_t processVtable[16]{};
    processVtable[0x68/8]=reinterpret_cast<uintptr_t>(prepareDecode);
    processVtable[0x70/8]=reinterpret_cast<uintptr_t>(fixedAvc);
    processVtable[0x78/8]=reinterpret_cast<uintptr_t>(submitDecode);
    put(processor,0,processVtable);put(picture,0x260,sps.data());
    put(sps,4,uint32_t{110});put(sps,0xc,uint32_t{1});put(sps,0x10,uint32_t{0});put(sps,0x14,uint32_t{2});put(sps,0x18,uint32_t{2});
    assert(nativeProcess(processor.data(),picture.data(),nullptr,nullptr,nullptr,message.data())==5);
    assert(prepared==1&&submitted==0);++checks;
    // 枚举全部 16 位 PCI ID，证明安全限制只作用于四个白名单核显。
    put(item,0,uint32_t{0x20});put(item,8,uint64_t{0x1000});put(item,0x20,uint32_t{64});put(item,0x24,uint32_t{64});request.count=1;
    for(unsigned id=0;id<=0xffff;id++){
        put(hardware,4,static_cast<uint16_t>(id));
        bool target=id==0x15e7||id==0x1636||id==0x1638||id==0x164c;
        assert(fixedCapability(caps.data(),&request)==(target?5:stockCapability(caps.data(),&request)));++checks;
    }
    assert(munmap(guardCode,4096)==0);
    printf("PASS %u native CPU checks: encoding limits, original errors, AVC SPS rejection and per-frame surfaces\n",checks);
}
