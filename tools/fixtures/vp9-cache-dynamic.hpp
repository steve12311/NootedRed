using namespace UserVP9DecodeData;
template<size_t Images, size_t Functions, size_t Sections, typename Slot, size_t Slots>
static void vp9Cache(const UserCacheResolver::ImageReference (&images)[Images],
                     const UserCacheResolver::Function (&functions)[Functions],
                     const UserCacheSectionData::Target (&sections)[Sections], const Slot (&slots)[Slots])
{
    cache(images, functions, true, sections, Sections);
    std::map<uint64_t, unsigned> tables;
    for (const auto& slot : slots) {
        uint8_t  field[4];
        uint64_t table;
        auto     address = functionAddresses[slot.constructor] + slot.field;
        assert(reader(address, field, 4) == 0 && UserCacheResolver::relativeTarget(address, 0, field, table));
        tables[table] = std::max(tables[table], slot.slot + 8);
    }
    for (auto [table, size] : tables) { blocks[table].resize(size); }
    for (const auto& slot : slots) {
        uint8_t  field[4];
        uint64_t table;
        auto     address = functionAddresses[slot.constructor] + slot.field;
        assert(reader(address, field, 4) == 0 && UserCacheResolver::relativeTarget(address, 0, field, table));
        word64(table + slot.slot, functionAddresses[slot.function] + slot.offset);
    }
}

static void vp9Cache()
{
    vp9Cache(ResolverImages, ResolverFunctions, SectionTargets, ABISlots);
}
