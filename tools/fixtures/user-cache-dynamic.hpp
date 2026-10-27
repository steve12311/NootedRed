// 测试缓存重排 Mach-O、完整函数与合并 GOT；各补丁刻意跨页。
#include <algorithm>
static std::map<uint64_t,std::vector<uint8_t>> blocks;
static std::map<uint64_t,int> protections;
static std::vector<uint64_t> imageAddresses,functionAddresses;
static std::vector<std::string> imagePaths;
struct MockSection { std::string segment,name; uint64_t address,size; };
static std::vector<std::vector<MockSection>> imageSections;
static bool sectionRebased=false;
static constexpr uint64_t base=0x7ff800200000ULL,unslid=base-0x200000;
static unsigned writes=0,protects=0,failWrite=0,failedProtect=0;
static std::set<unsigned> failedProtectCalls;
static uint64_t failedRead=0;
static void put(uint64_t a,const void* p,size_t n){
 auto i=blocks.upper_bound(a);assert(i!=blocks.begin());--i;
 assert(a-i->first<=i->second.size()&&n<=i->second.size()-(a-i->first));memcpy(i->second.data()+a-i->first,p,n);
}
static void word32(uint64_t a,uint32_t v){put(a,&v,4);}
static void word64(uint64_t a,uint64_t v){put(a,&v,8);}
static int reader(uint64_t a,void* p,size_t n){
 if(a==0x1000){assert(n==8);memcpy(p,&base,8);return 0;}
 if(a==failedRead)return 1;
 auto i=blocks.upper_bound(a);if(i==blocks.begin())return 1;--i;
 if(a-i->first>i->second.size()||n>i->second.size()-(a-i->first))return 1;
 memcpy(p,i->second.data()+a-i->first,n);return 0;
}
static void rel(uint64_t a,uint32_t offset,uint64_t target){uint8_t b[4];
 assert(UserSharedCache::setRelative(b,0,a+offset,target));put(a+offset,b,4);
}
static uint64_t destination(const UserCacheResolver::Reference& ref,const UserCacheResolver::Function* functions,unsigned count){
 for(unsigned f=0;f<count;f++)if(functions[f].image==ref.image&&ref.rva>=functions[f].rva&&ref.rva-functions[f].rva<functions[f].size)
  return functionAddresses[f]+ref.rva-functions[f].rva;
 if(sectionRebased)for(auto& target:UserCacheSectionData::Targets)
  if(target.rva==ref.rva && imagePaths[ref.image]==target.path)
   for(auto& section:imageSections[ref.image])if(section.segment==target.segment&&section.name==target.section)
    return section.address+target.offset;
 return imageAddresses[ref.image]+ref.rva;
}
static void sectionCommands(uint64_t a,const char* path,unsigned image){
 std::map<std::pair<std::string,std::string>,uint64_t> sizes;
 for(auto& t:UserCacheSectionData::Targets)if(strcmp(t.path,path)==0)
  sizes[{t.segment,t.section}]=std::max(sizes[{t.segment,t.section}],t.offset+4);
 auto& sections=imageSections[image];
 sections.push_back({"__TEXT","__text",a+0x1000,0x6f000});
 sections.push_back({"__TEXT","__const",a+0x71000,0x1000});
 unsigned index=0;
 for(auto&[names,size]:sizes)sections.push_back({names.first,names.second,a+0x10000000+(index++)*0x100000,size});
 std::vector<std::string> segments{"__TEXT","__DATA"};
 for(auto& s:sections)if(std::find(segments.begin(),segments.end(),s.segment)==segments.end())segments.push_back(s.segment);
 word32(a+16,segments.size());unsigned cursor=32;
 for(auto& name:segments){
  unsigned count=0;uint64_t first=~uint64_t(0),last=0;
  for(auto& s:sections)if(s.segment==name){count++;first=std::min(first,s.address);last=std::max(last,s.address+s.size);}
  if(name=="__DATA"){first=a+0x80000;last=a+0x78000000;}
  if(name=="__TEXT"){first=a;last=a+0x80000;}
  auto c=a+cursor;unsigned length=72+count*80;
  std::vector<uint8_t> zero(length);put(c,zero.data(),zero.size());
  word32(c,0x19);word32(c+4,length);put(c+8,name.c_str(),name.size());
  word64(c+24,first-0x200000);word64(c+32,last-first);word32(c+56,name=="__TEXT"?5:3);word32(c+60,name=="__TEXT"?5:3);word32(c+64,count);
  auto at=c+72;
  for(auto& s:sections)if(s.segment==name){
   put(at,s.name.data(),s.name.size());put(at+16,name.data(),name.size());word64(at+32,s.address-0x200000);word64(at+40,s.size);at+=80;
  }
  cursor+=length;
 }
 word32(a+20,cursor-32);
}
template<size_t I,size_t F>static void cache(const UserCacheResolver::ImageReference(&images)[I],const UserCacheResolver::Function(&functions)[F],bool rebase=false){
 blocks.clear();protections.clear();properties.clear();failedProtectCalls.clear();imageAddresses.clear();functionAddresses.clear();
 writes=protects=failWrite=failedProtect=0;failedRead=0;
 sectionRebased=rebase;imagePaths.clear();imageSections.clear();imageSections.resize(I);
 blocks[base].resize(0x20000);put(base,"dyld_v1 x86_64h",16);memset(blocks[base].data()+88,0xa7,16);
 word32(base+16,0x300);word32(base+20,1);word64(base+224,unslid);word64(base+232,0x400000000ULL);word64(base+240,0x200000);
 word32(base+448,0x1000);word32(base+452,I);word64(base+0x300,unslid);word64(base+0x308,0x20000);word32(base+0x31c,1);
 for(unsigned i=0;i<I;i++){
  // 非顺序映像表，且每个映像的位置与参考缓存不同。
  auto a=base+0x1000000+i*0x100000;imageAddresses.push_back(a);blocks[a].resize(0x80000);
  imagePaths.emplace_back(images[i].path);
  word64(base+0x1000+i*32,a-0x200000);word32(base+0x1018+i*32,0x3000+i*256);put(base+0x3000+i*256,images[i].path,images[i].size);
  word32(a,0xfeedfacf);word32(a+4,0x1000007);word32(a+12,8);word32(a+16,2);word32(a+20,304);
  auto c=a+32;word32(c,0x19);word32(c+4,232);put(c+8,"__TEXT",7);word64(c+24,a-0x200000);word64(c+32,0x80000);
  word32(c+56,5);word32(c+60,5);word32(c+64,2);
  auto s=c+72;put(s,"__text",7);put(s+16,"__TEXT",7);word64(s+32,a+0x1000-0x200000);word64(s+40,0x6f000);
  s+=80;put(s,"__const",8);put(s+16,"__TEXT",7);word64(s+32,a+0x71000-0x200000);word64(s+40,0x1000);
  c+=232;word32(c,0x19);word32(c+4,72);put(c+8,"__DATA",7);word64(c+24,a+0x80000-0x200000);word64(c+32,0x78000000);word32(c+60,3);
  if(rebase)sectionCommands(a,images[i].path,i);
 }
 unsigned next[I]{};
 for(unsigned f=0;f<F;f++){
  auto i=functions[f].image;
  auto a=imageAddresses[i]+0x3f80+next[i];next[i]+=((functions[f].size+4095)/4096+2)*4096;
  assert(next[i]<0x60000);functionAddresses.push_back(a);put(a,functions[f].original,functions[f].size);
 }
 unsigned binding=0;
 for(unsigned f=0;f<F;f++)for(unsigned r=0;r<functions[f].originalCount;r++){
  auto ref=functions[f].originalRefs[r];auto target=destination(ref,functions,F);
  if(ref.indirect){
   auto slot=base+0x7000000+binding++*32;blocks[slot].resize(32);
   if(ref.indirect==2){uint8_t stub[6]={0xff,0x25};put(slot,stub,6);rel(slot,2,slot+8);word64(slot+8,target);}
   else word64(slot,target);
   target=slot;
  }
  rel(functionAddresses[f],ref.offset,target);
 }
}
template<typename Patch>static void pages(const Patch* patches,unsigned count){
 for(unsigned i=0;i<count;i++){
  auto a=base+patches[i].offset;
  for(auto p=a&~uint64_t(4095);p<=((a+patches[i].size-1)&~uint64_t(4095));p+=4096)protections[p]=5;
 }
}
static int writer(const void* p,uint64_t a,size_t n){
 writes++;for(auto page=a&~uint64_t(4095);page<=((a+n-1)&~uint64_t(4095));page+=4096)assert(protections.at(page)==3);
 if(writes==failWrite){put(a,p,n>2?2:n);return 1;}put(a,p,n);return 0;
}
static void rx(){for(auto[a,p]:protections){(void)a;assert(p==5);}}
