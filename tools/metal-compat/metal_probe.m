#import <Foundation/Foundation.h>
#include <IOKit/IOKitLib.h>
#import <Metal/Metal.h>
#include <dlfcn.h>
#import <objc/runtime.h>
#include <signal.h>
#include <stdio.h>
#include <sys/sysctl.h>
#include <sys/wait.h>
#include <unistd.h>
static int test(void) {
    @autoreleasepool {
        io_service_t service = 0;
        for (int i = 0; i < 30; i++) {
            service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IOAccelerator"));
            if (service)
                break;
            sleep(1);
        }
        if (!service) {
            puts("FAIL: no IOAccelerator within 30s");
            return 10;
        }
        IOObjectRelease(service);
        NSArray<id<MTLDevice>> *devices = MTLCopyAllDevices();
        printf("Metal device count=%lu\n", (unsigned long)devices.count);
        fflush(stdout);
        if (!devices.count)
            return 11;
        id<MTLDevice> device = nil;
        for (id<MTLDevice> candidate in devices)
            if ([candidate.name containsString:@"AMD"]) {
                device = candidate;
                break;
            }
        if (!device) {
            puts("SKIP: no AMD Metal device");
            return 77;
        }
        printf("Metal device=%s\n", device.name.UTF8String);
        fflush(stdout);
        Class cmd = objc_getClass("GFX9AMD_MtlCmdBuffer");
        Ivar members = class_getInstanceVariable(cmd, "m_members");
        if (cmd && members)
            printf("CmdBuffer superclass size=%zu m_members=%#tx\n",
                   class_getInstanceSize(class_getSuperclass(cmd)), ivar_getOffset(members));
        id<MTLCommandQueue> queue = [device newCommandQueue];
        if (!queue)
            return 12;
        MTLTextureDescriptor *desc =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                               width:4
                                                              height:4
                                                           mipmapped:NO];
        desc.storageMode = MTLStorageModeManaged;
        desc.usage = MTLTextureUsageRenderTarget;
        id<MTLTexture> texture = [device newTextureWithDescriptor:desc];
        if (!texture)
            return 13;
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(1, 0, 0, 1);
        id<MTLCommandBuffer> buffer = [queue commandBuffer];
        if (!buffer)
            return 14;
        id<MTLRenderCommandEncoder> render = [buffer renderCommandEncoderWithDescriptor:pass];
        if (!render)
            return 15;
        [render endEncoding];
        id<MTLBlitCommandEncoder> blit = [buffer blitCommandEncoder];
        if (!blit)
            return 16;
        [blit synchronizeResource:texture];
        [blit endEncoding];
        puts("Submitting 4x4 GPU clear/readback");
        fflush(stdout);
        [buffer commit];
        [buffer waitUntilCompleted];
        if (buffer.status != MTLCommandBufferStatusCompleted) {
            printf("FAIL: command status=%lu error=%s\n", (unsigned long)buffer.status,
                   buffer.error.description.UTF8String);
            return 17;
        }
        unsigned char pixels[64] = {0};
        [texture getBytes:pixels bytesPerRow:16 fromRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0];
        for (unsigned i = 0; i < 64; i += 4)
            if (pixels[i] != 255 || pixels[i + 1] || pixels[i + 2] || pixels[i + 3] != 255) {
                printf("FAIL: pixel %u is %u,%u,%u,%u\n", i / 4, pixels[i], pixels[i + 1], pixels[i + 2],
                       pixels[i + 3]);
                return 18;
            }
        puts("PASS: GPU clear/readback. Compiling vertex + fragment shaders next.");
        NSError *error = nil;
        NSString *source = @"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 vs(uint i "
                           @"[[vertex_id]]) { float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)}; return "
                           @"float4(p[i],0,1); }\nfragment float4 fs() { return float4(0,1,0,1); }\n";
        id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&error];
        if (!library) {
            printf("FAIL: shader library: %s\n", error.description.UTF8String);
            return 20;
        }
        puts("Shader library compiled");
        MTLRenderPipelineDescriptor *pipelineDesc = [MTLRenderPipelineDescriptor new];
        pipelineDesc.vertexFunction = [library newFunctionWithName:@"vs"];
        pipelineDesc.fragmentFunction = [library newFunctionWithName:@"fs"];
        pipelineDesc.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
        id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:pipelineDesc
                                                                                     error:&error];
        if (!pipeline) {
            printf("FAIL: shader pipeline: %s\n", error.description.UTF8String);
            return 21;
        }
        puts("Render pipeline created");
        buffer = [queue commandBuffer];
        render = [buffer renderCommandEncoderWithDescriptor:pass];
        [render setViewport:(MTLViewport){0, 0, 4, 4, 0, 1}];
        [render setScissorRect:(MTLScissorRect){0, 0, 4, 4}];
        [render setCullMode:MTLCullModeNone];
        [render setRenderPipelineState:pipeline];
        [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [render endEncoding];
        blit = [buffer blitCommandEncoder];
        [blit synchronizeResource:texture];
        [blit endEncoding];
        [buffer commit];
        [buffer waitUntilCompleted];
        if (buffer.status != MTLCommandBufferStatusCompleted) {
            printf("FAIL: shader draw: %s\n", buffer.error.description.UTF8String);
            return 22;
        }
        [texture getBytes:pixels bytesPerRow:16 fromRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0];
        int drawFail = 0;
        for (unsigned i = 0; i < 64; i += 4) {
            printf("shader pixel %u = %u,%u,%u,%u\n", i / 4, pixels[i], pixels[i + 1], pixels[i + 2],
                   pixels[i + 3]);
            if (pixels[i] || pixels[i + 1] != 255 || pixels[i + 2] || pixels[i + 3] != 255)
                drawFail = 1;
        }
        puts(drawFail ? "FAIL: triangle readback"
                      : "PASS: runtime shader compilation + render pipeline + triangle draw + 16-pixel GPU "
                        "readback");
        void (*stats)(unsigned long *, unsigned long *) = dlsym(RTLD_DEFAULT, "NRedMetalCompatStats");
        if (stats) {
            unsigned long c = 0, p = 0;
            stats(&c, &p);
            printf("NRedMetalCompat colors=%lu pipelines=%lu\n", c, p);
        }
        NSString *computeSource =
            @"#include <metal_stdlib>\nusing namespace metal; kernel void writeValues(device uint *out "
            @"[[buffer(0)]], uint i [[thread_position_in_grid]]) { out[i]=0x12340000u+i; }";
        error = nil;
        library = [device newLibraryWithSource:computeSource options:nil error:&error];
        if (!library) {
            printf("FAIL: compute library %s\n", error.description.UTF8String);
            return 24;
        }
        id<MTLComputePipelineState> cp =
            [device newComputePipelineStateWithFunction:[library newFunctionWithName:@"writeValues"]
                                                  error:&error];
        if (!cp) {
            printf("FAIL: compute pipeline %s\n", error.description.UTF8String);
            return 25;
        }
        id<MTLBuffer> result = [device newBufferWithLength:64 options:MTLResourceStorageModeShared];
        memset(result.contents, 0, 64);
        buffer = [queue commandBuffer];
        id<MTLComputeCommandEncoder> ce = [buffer computeCommandEncoder];
        [ce setComputePipelineState:cp];
        [ce setBuffer:result offset:0 atIndex:0];
        [ce dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(16, 1, 1)];
        [ce endEncoding];
        [buffer commit];
        [buffer waitUntilCompleted];
        if (buffer.status != MTLCommandBufferStatusCompleted) {
            printf("FAIL: compute command %s\n", buffer.error.description.UTF8String);
            return 26;
        }
        unsigned *values = result.contents;
        int computeFail = 0;
        for (unsigned i = 0; i < 16; i++)
            if (values[i] != (0x12340000u + i)) {
                printf("FAIL: compute word%u=%#x\n", i, values[i]);
                computeFail = 1;
                break;
            }
        if (!computeFail)
            puts("PASS: compute shader dispatch + 16-word readback");
        return drawFail ? 23 : computeFail ? 27 : 0;
    }
}
int main(int argc, char **argv) {
    if (argc != 2 || (strcmp(argv[1], "--run") && strcmp(argv[1], "--worker"))) {
        fprintf(stderr, "Usage: %s --run (submits small GPU workloads)\n", argv[0]);
        return 2;
    }
    setbuf(stdout, NULL);
    setbuf(stderr, NULL);
    if (argc == 2 && strcmp(argv[1], "--worker") == 0)
        return test();
    puts("NRed Metal descriptor compatibility; bounded one-shot test");
    pid_t child = fork();
    if (child == 0) {
        setpgid(0, 0);
        execl(argv[0], argv[0], "--worker", (char *)0);
        _exit(127);
    }
    if (child < 0)
        return 3;
    setpgid(child, child);
    int status = 0;
    for (int i = 0; i < 90; i++) {
        if (waitpid(child, &status, WNOHANG) == child) {
            printf("probe wait_status=%d\n", status);
            return WIFEXITED(status) ? WEXITSTATUS(status) : 4;
        }
        sleep(1);
    }
    kill(-child, SIGKILL);
    kill(child, SIGKILL);
    puts("FAIL: probe exceeded 90s; no reboot or retry");
    return 5;
}
