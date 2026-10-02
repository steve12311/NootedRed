// 对照普通和双源混合的真实像素输出；首次错误或 GPU 超时即停止。
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <math.h>
#include <stdio.h>
#include <unistd.h>
#include <string.h>

static int expectedDisableQuad = -1;
static int observedDisableQuad[2] = {-1, -1};

int main(int argc, const char* argv[])
{
    @autoreleasepool {
        setbuf(stdout, NULL);
        const BOOL inspectOnly = argc == 2 && strcmp(argv[1], "--inspect-only") == 0;
        const BOOL render = argc == 2 && strcmp(argv[1], "--render") == 0;
        if (!inspectOnly && !render) {
            fprintf(stderr, "用法：%s --inspect-only | --render\n", argv[0]);
            return 2;
        }
        NSOperatingSystemVersion version = NSProcessInfo.processInfo.operatingSystemVersion;
        if (version.majorVersion != 26 || version.minorVersion != 7 || version.patchVersion != 1) { return 6; }
        if (render) { expectedDisableQuad = 1; }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) { return 2; }
        NSError* error = nil;
        NSString* text = @"#include <metal_stdlib>\nusing namespace metal;\n"
            "vertex float4 v(uint i [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0,1);}\n"
            "fragment half4 single(){return half4(0.125,0.25,0.375,0.5);}\n"
            "struct Out{half4 a [[color(0),index(0)]];half4 b [[color(0),index(1)]];};\n"
            "fragment Out dual(){return {half4(0.125,0.25,0.375,0.5),half4(0.25,0.5,0.75,0.25)};}\n";
        id<MTLLibrary> library = [device newLibraryWithSource:text options:nil error:&error];
        id<MTLCommandQueue> queue = [device newCommandQueue];
        if (!library || !queue) { fprintf(stderr, "setup: %s\n", error.description.UTF8String); return 3; }
        for (unsigned dual = 0; dual <= 1; ++dual) {
            MTLRenderPipelineDescriptor* desc = [MTLRenderPipelineDescriptor new];
            desc.vertexFunction = [library newFunctionWithName:@"v"];
            desc.fragmentFunction = [library newFunctionWithName:dual ? @"dual" : @"single"];
            MTLRenderPipelineColorAttachmentDescriptor* attachment = desc.colorAttachments[0];
            attachment.pixelFormat = MTLPixelFormatRGBA8Unorm; attachment.blendingEnabled = YES;
            attachment.sourceRGBBlendFactor = MTLBlendFactorOne; attachment.sourceAlphaBlendFactor = MTLBlendFactorOne;
            attachment.destinationRGBBlendFactor = dual ? MTLBlendFactorOneMinusSource1Alpha : MTLBlendFactorOneMinusSourceAlpha;
            attachment.destinationAlphaBlendFactor = attachment.destinationRGBBlendFactor;
            id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:desc error:&error];
            if (!pipeline) { fprintf(stderr, "pipeline: %s\n", error.description.UTF8String); return 3; }
            Ivar member = class_getInstanceVariable(object_getClass(pipeline), "m_members");
            if (!member || strncmp(ivar_getTypeEncoding(member), "{GFX9_RenderPipelineStateMembersRec=",
                                  sizeof("{GFX9_RenderPipelineStateMembersRec=")-1) != 0) { return 6; }
            const ptrdiff_t offset = ivar_getOffset(member);
            if (offset < 0 || (size_t)offset + 0x2c > class_getInstanceSize(object_getClass(pipeline))) { return 6; }
            uint32_t control = 0;
            memcpy(&control, (const uint8_t*)(__bridge const void*)pipeline + offset + 0x28, 4);
            observedDisableQuad[dual] = (int)(control&1);
            printf("pipeline dual=%u CB_COLOR_CONTROL=0x%08x DISABLE_DUAL_QUAD=%u\n",dual,control,control&1);
            if (expectedDisableQuad >= 0 && (int)(control&1) != expectedDisableQuad) { return 6; }
            if (inspectOnly) { continue; }
            MTLTextureDescriptor* textureDesc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                width:32 height:32 mipmapped:NO];
            textureDesc.storageMode = MTLStorageModePrivate; textureDesc.usage = MTLTextureUsageRenderTarget;
            id<MTLTexture> texture = [device newTextureWithDescriptor:textureDesc];
            id<MTLBuffer> readback = [device newBufferWithLength:256*32 options:MTLResourceStorageModeShared];
            if (!texture || !readback.contents) { return 3; }
            MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
            pass.colorAttachments[0].texture = texture; pass.colorAttachments[0].clearColor = MTLClearColorMake(0.5,0.5,0.5,0.5);
            pass.colorAttachments[0].loadAction = MTLLoadActionClear; pass.colorAttachments[0].storeAction = MTLStoreActionStore;
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
            [encoder setRenderPipelineState:pipeline];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [encoder endEncoding];
            id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
            [blit copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
                sourceSize:MTLSizeMake(32,32,1) toBuffer:readback destinationOffset:0 destinationBytesPerRow:256
                destinationBytesPerImage:256*32]; [blit endEncoding];
            printf("submit dual=%u\n", dual); [command commit];
            const double deadline = NSProcessInfo.processInfo.systemUptime + 10;
            while (command.status < MTLCommandBufferStatusCompleted
                   && NSProcessInfo.processInfo.systemUptime < deadline) { usleep(10000); }
            if (command.status != MTLCommandBufferStatusCompleted) {
                fprintf(stderr, "dual=%u GPU failed/timed out status=%lu error=%s\n",dual,(unsigned long)command.status,
                    command.error.description.UTF8String ?: "none"); return 4;
            }
            const double source[] = {0.125,0.25,0.375,0.5};
            const uint8_t* bytes = (const uint8_t*)readback.contents;
            printf("pixel dual=%u actual=%u,%u,%u,%u\n",dual,bytes[0],bytes[1],bytes[2],bytes[3]);
            for (unsigned y=0;y<32;++y) for (unsigned x=0;x<32*4;++x) {
                const int expected = (int)lround((source[x%4] + 0.5 * (dual ? 0.75 : 0.5))*255);
                if (abs((int)bytes[y*256+x]-expected)>1) {
                    fprintf(stderr, "dual=%u mismatch y=%u byte=%u expected=%d actual=%u\n",dual,y,x,expected,bytes[y*256+x]);
                    return 5;
                }
            }
            printf("PASS: dual=%u output matches blend equation\n",dual);
        }
        if (inspectOnly) { printf("pipelineDisableDualQuad=%d,%d\n",observedDisableQuad[0],observedDisableQuad[1]); }
    }
    return 0;
}
