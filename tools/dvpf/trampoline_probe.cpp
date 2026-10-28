// DVPF SubmitGuard 跳板生命周期探针：故障注入、入口恢复和目标页保留。
#include <VPF/ExecutableTrampolineResource.hpp>
#include <cstdio>

using namespace VPF;

static unsigned failures = 0;

static void check(const bool condition, const char* label)
{
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", label);
        ++failures;
    }
}

int main()
{
    ExecutableTrampolineResource empty;
    check(!empty.fill(16) && empty.record().fault == TrampolineFault::FillFailed, "fill before allocate");
    check(!empty.makeExecutable() && empty.record().fault == TrampolineFault::ProtectionFailed,
          "executable before fill");
    check(!empty.linkEntry(0x1000) && empty.record().fault == TrampolineFault::EntryLinkFailed,
          "link before executable");
    check(!empty.allocate(0), "allocate zero");
    check(empty.allocate(0x1000), "allocate");
    check(!empty.allocate(0x2000), "allocate twice");
    check(empty.fill(16), "fill");
    check(!empty.fill(16), "fill twice");
    check(empty.makeExecutable(), "make executable");
    check(!empty.linkEntry(0), "link zero");
    check(empty.linkEntry(0x2000), "link");
    check(!empty.linkEntry(0x3000), "link twice");
    check(empty.record().state == TrampolineState::EntryLinked, "entry linked");

    // 入口仍在引用时不得释放；入口恢复失败必须保留页面。
    check(!empty.release() && empty.record().fault == TrampolineFault::ReleaseRejected, "release linked");
    check(!empty.verify(false) && empty.record().state == TrampolineState::Retained, "verify mismatch");
    check(empty.pageRetained() && empty.linked(), "retained after verify");
    check(!empty.release(), "release retained");
    check(!empty.restoreEntry(false) && empty.record().state == TrampolineState::Retained, "restore failed");
    check(empty.restoreEntry(true), "restore ok");
    check(empty.record().state == TrampolineState::Verified && !empty.record().entryLinked, "restored verified");
    check(empty.release() && empty.record().state == TrampolineState::Released, "release verified");
    check(empty.invariant(), "invariant after release");

    // 已安装且入口仍链接的 Verified 状态也必须保留页面。
    ExecutableTrampolineResource installed;
    check(installed.allocate(0x4000) && installed.fill(32) && installed.makeExecutable(), "installed setup");
    check(installed.linkEntry(0x5000) && installed.verify(true), "installed verify");
    check(installed.record().state == TrampolineState::Verified && installed.linked(), "installed linked");
    check(!installed.release(), "installed release rejected");
    check(installed.restoreEntry(true) && installed.release() && installed.invariant(), "installed uninstall");

    // 回滚：入口已恢复才释放；未恢复则保留。
    ExecutableTrampolineResource rollback;
    check(rollback.allocate(0x6000) && rollback.fill(32) && rollback.makeExecutable(), "rollback setup");
    check(rollback.linkEntry(0x7000), "rollback link");
    auto record = rollback.rollback(false);
    check(record.state == TrampolineState::Retained && record.pageRetained && record.entryLinked, "rollback retained");
    record = rollback.rollback(true);
    check(record.state == TrampolineState::Released && !record.entryLinked && !record.pageRetained,
          "rollback released");

    // 尚未链接入口的分配可以安全释放。
    ExecutableTrampolineResource plain;
    check(plain.allocate(0x8000) && plain.release() && plain.record().state == TrampolineState::Released,
          "plain release");
    check(plain.rollback(true).state == TrampolineState::Released, "plain rollback");

    if (failures) {
        std::fprintf(stderr, "FAILED: %u checks\n", failures);
        return 1;
    }
    std::printf("PASS DVPF trampoline lifecycle probe\n");
    return 0;
}
