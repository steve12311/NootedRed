using namespace UserVP9DecodeData;
static void vp9Cache()
{
    cache(ResolverImages, ResolverFunctions, true, SectionTargets, UserVP9DecodeResolver::SectionCount);
    std::map<uint64_t, unsigned> tables;
    for (const auto& slot : ABISlots) {
        uint8_t  field[4];
        uint64_t table;
        auto     address = functionAddresses[slot.constructor] + slot.field;
        assert(reader(address, field, 4) == 0 && UserCacheResolver::relativeTarget(address, 0, field, table));
        tables[table] = std::max(tables[table], slot.slot + 8);
    }
    for (auto [table, size] : tables) { blocks[table].resize(size); }
    for (const auto& slot : ABISlots) {
        uint8_t  field[4];
        uint64_t table;
        auto     address = functionAddresses[slot.constructor] + slot.field;
        assert(reader(address, field, 4) == 0 && UserCacheResolver::relativeTarget(address, 0, field, table));
        word64(table + slot.slot, functionAddresses[slot.function] + slot.offset);
    }
}
