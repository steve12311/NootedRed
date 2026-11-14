"""VP9 phase V2: NootedRed/VP9Message.hpp must build the same VCN message as Mesa, byte for byte.

Reference path (real Mesa code, see tools/fetch_vp9_mesa.sh): ffmpeg's CBS parser reads each frame header, the test
fills the VA-API picture/slice parameters the way ffmpeg's vaapi_vp9 does, then Mesa's VA frontend
(picture_vp9.c), si_dec_vp9 and build_vp9_msg produce the message. NootedRed path: only the frame bytes and the
surfaces, like AppleGVA gives them. Clips come from the local libvpx encoder (`ffmpeg -c:v libvpx-vp9`).

Usage: python3 tools/test_vp9_message.py [--write-probs]   (--write-probs regenerates NootedRed/VP9DefaultProbs.hpp)
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MESA = ROOT / 'build/vp9/mesa'
WORK = ROOT / 'build/vp9-message'
COMMIT = '767fbf7c6492f64549c90e9a080ab5417895a9ec'
PROBS_HPP = ROOT / 'NootedRed/VP9DefaultProbs.hpp'

# name, size, frames, encoder options, pixel format, P010 output
CLIPS = [
    ('basic', '352x288', 30, ['-b:v', '200k'], 'yuv420p', False),
    ('altref-cyclic-aq', '640x360', 40, ['-b:v', '300k', '-aq-mode', '3', '-auto-alt-ref', '1', '-lag-in-frames', '16',
                                         '-tile-columns', '1'], 'yuv420p', False),
    ('tiles-variance-aq', '1280x720', 12, ['-b:v', '400k', '-aq-mode', '1', '-tile-columns', '2', '-tile-rows', '1',
                                           '-sharpness', '3', '-cpu-used', '8', '-deadline', 'realtime'],
     'yuv420p', False),
    ('error-resilient-odd', '350x198', 25, ['-b:v', '150k', '-error-resilient', '1', '-lag-in-frames', '0',
                                            '-frame-parallel', '0'], 'yuv420p', False),
    ('lossless', '176x144', 8, ['-lossless', '1'], 'yuv420p', False),
    ('complexity-aq-kf', '320x240', 40, ['-b:v', '120k', '-aq-mode', '2', '-g', '10', '-auto-alt-ref', '1',
                                         '-lag-in-frames', '12', '-arnr-maxframes', '5'], 'yuv420p', False),
    ('two-pass-altref', '640x360', 40, ['-b:v', '300k', '-auto-alt-ref', '1', '-lag-in-frames', '25',
                                        '-arnr-maxframes', '7', '-arnr-strength', '5', '-tile-columns', '2', 'two-pass'],
     'yuv420p', False),
    ('profile2-p010', '320x180', 12, ['-b:v', '200k'], 'yuv420p10le', True),
    ('profile2-dither', '320x180', 12, ['-b:v', '200k', '-aq-mode', '3'], 'yuv420p10le', False),
]

AC_STUBS = '''enum ac_video_codec { AC_VIDEO_CODEC_AVC, AC_VIDEO_CODEC_HEVC, AC_VIDEO_CODEC_VP9, AC_VIDEO_CODEC_AV1,
   AC_VIDEO_CODEC_MPEG2, AC_VIDEO_CODEC_VC1, AC_VIDEO_CODEC_MJPEG };
enum ac_video_subsample { AC_VIDEO_SUBSAMPLE_420, AC_VIDEO_SUBSAMPLE_422, AC_VIDEO_SUBSAMPLE_444, AC_VIDEO_SUBSAMPLE_400 };
enum ac_video_dec_tier { AC_VIDEO_DEC_TIER0 = 1, AC_VIDEO_DEC_TIER1 = 2, AC_VIDEO_DEC_TIER2 = 4, AC_VIDEO_DEC_TIER3 = 8 };
struct ac_video_surface { unsigned format; unsigned size; };
'''


def function(source, name):
    """Text of a top-level C function, return type included."""
    m = re.search(rf'^(?:[^\n;{{}}]*\n)?[^\n;{{}}]*\b{name}\(', source, re.M)
    assert m, name
    start = m.start()
    first = source[start:source.index('\n', start)]
    if first.strip() and not first.lstrip().startswith(('static', 'void', 'unsigned', 'int', 'uint')):
        start = source.index('\n', start) + 1
    return source[start:source.index('\n}\n', m.end()) + 3]


def struct(source, name):
    m = re.search(rf'^struct {name}\n\{{', source, re.M)
    assert m, name
    return source[m.start():source.index('\n};\n', m.end()) + 4]


def build_reference():
    missing = [f for f in ('ac_vcn_dec.c', 'ac_vcn_dec.h', 'ac_video_dec.h', 'ac_vcn_vp9_default.h', 'p_video_state.h',
                           'si_video_dec.c', 'picture_vp9.c', 'vl_vlc.h', 'COMMIT') if not (MESA / f).exists()]
    if missing or (MESA / 'COMMIT').read_text().strip() != COMMIT:
        sys.exit(f'Mesa reference missing or at another commit: run tools/fetch_vp9_mesa.sh ({missing})')
    stubs = WORK / 'stubs'
    (stubs / 'util').mkdir(parents=True, exist_ok=True)
    (stubs / 'ac_video.h').write_text(AC_STUBS)
    (stubs / 'ac_gpu_info.h').write_text('enum amd_ip_type { AMD_IP_VCN_DEC };\n')
    (stubs / 'util/u_math.h').write_text('')
    pipe = (MESA / 'p_video_state.h').read_text()
    picture = (MESA / 'picture_vp9.c').read_text()
    picture = picture.replace('#include "util/vl_vlc.h"', '').replace('#include "va_private.h"', '')
    vcn = (MESA / 'ac_vcn_dec.c').read_text()
    si = (MESA / 'si_video_dec.c').read_text()
    text = (ROOT / 'tools/fixtures/vp9-mesa-ref.c.in').read_text()
    text = text.replace('@@AC_TYPES@@', '#include "ac_vcn_dec.h"')
    text = text.replace('@@PIPE_TYPES@@', struct(pipe, 'vp9_segment_parameter') + struct(pipe, 'pipe_vp9_picture_desc'))
    text = text.replace('@@PICTURE_VP9@@', picture)
    defines = ''.join(l + '\n' for l in vcn.splitlines() if l.startswith('#define RDECODE_DITHERING_'))
    text = text.replace('@@AC_VCN_FUNCS@@', defines + '\n'.join(function(vcn, n) for n in
                                                      ('ac_vcn_vp9_fill_probs_table', 'calc_ctx_size_vp9',
                                                       'build_vp9_msg')))
    text = text.replace('@@SI_DEC_VP9@@', function(si, 'si_dec_vp9'))
    source = WORK / 'mesa_ref.c'
    source.write_text(text)
    obj = WORK / 'mesa_ref.o'
    subprocess.run(['clang', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-w', '-c',
                    '-I' + str(stubs), '-I' + str(MESA), '-I' + str(ROOT / 'tools/fixtures'), str(source), '-o',
                    str(obj)], check=True, timeout=120)
    return obj


def make_clip(name, size, frames, options, pix_fmt):
    ivf = WORK / f'{name}.ivf'
    if ivf.exists():
        return ivf
    passes = [['-pass', '1', '-passlogfile', str(WORK / name)], ['-pass', '2', '-passlogfile', str(WORK / name)]] \
        if 'two-pass' in options else [[]]
    options = [o for o in options if o != 'two-pass']
    for i, extra in enumerate(passes):
        output = ['-f', 'null', '-'] if i < len(passes) - 1 else ['-f', 'ivf', str(ivf)]
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                        f'testsrc2=s={size}:r=30', '-f', 'lavfi', '-i', f'mandelbrot=s={size}:r=30',
                        '-filter_complex', '[0][1]blend=all_mode=average', '-frames:v', str(frames),
                        '-pix_fmt', pix_fmt, '-c:v', 'libvpx-vp9', *options, *extra, *output], check=True, timeout=300)
    return ivf


def trace(ivf):
    """Per-frame header syntax elements from ffmpeg's CBS parser: [{name: (bit position, value)}]."""
    out = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(ivf), '-c', 'copy', '-bsf:v', 'trace_headers',
                          '-f', 'null', '-'], capture_output=True, text=True, check=True, timeout=120).stderr
    frames, current = [], None
    for line in out.splitlines():
        if '] Packet:' in line or '] Superframe Index' in line:
            current = None
        elif line.endswith('] Frame'):
            current = {}
            frames.append(current)
        elif current is not None:
            m = re.match(r'\[trace_headers @ \w+\] (\d+)\s+(\S+)\s+[01]+ = (-?\d+)$', line)
            if m:
                current[m.group(2)] = (int(m.group(1)), int(m.group(3)))
    return frames


def va_records(frames):
    """VA-API parameters as ffmpeg's vaapi_vp9 fills them, plus a surface pool like a VA decoder's."""
    slot_size = [(0, 0)] * 8
    slot_surface = [0] * 8
    tree, pred = [255] * 7, [255] * 3
    seg_ref = [(0, 0, 0)] * 8
    bit_depth = 8
    records = []
    for f in frames:
        v = lambda k, d=0: f[k][1] if k in f else d
        if v('show_existing_frame'):
            records.append(None)
            continue
        profile = v('profile_low_bit') | v('profile_high_bit') << 1
        key, intra = v('frame_type') == 0, v('intra_only')
        if profile == 0:
            bit_depth = 8
        if 'ten_or_twelve_bit' in f:
            bit_depth = 12 if v('ten_or_twelve_bit') else 10
        r = dict(prof=profile, bd=bit_depth, ft=v('frame_type'), sf=v('show_frame'), er=v('error_resilient_mode'),
                 io=intra, rfc=v('reset_frame_context'), ss=1)
        refidx, bias = [0, 0, 0], [0, 0, 0]
        if key or intra:
            w, h = v('frame_width_minus_1') + 1, v('frame_height_minus_1') + 1
            refresh = 0xFF if key else v('refresh_frame_flags')
            r.update(hp=0, mf=0)
        else:
            refresh = v('refresh_frame_flags')
            refidx = [v(f'ref_frame_idx[{i}]') for i in range(3)]
            bias = [v(f'ref_frame_sign_bias[{i + 1}]') for i in range(3)]
            found = next((i for i in range(3) if v(f'found_ref[{i}]')), None)
            w, h = slot_size[refidx[found]] if found is not None else (v('frame_width_minus_1') + 1,
                                                                        v('frame_height_minus_1') + 1)
            r.update(hp=v('allow_high_precision_mv'),
                     mf=4 if v('is_filter_switchable') else (1, 0, 2, 3)[v('raw_interpolation_filter_type')])
        er = r['er']
        r.update(fw=w, fh=h, rc=0 if er else v('refresh_frame_context'), fp=1 if er else v('frame_parallel_decoding_mode'),
                 fci=v('frame_context_idx'), fl=v('loop_filter_level'), sl=v('loop_filter_sharpness'),
                 ltc=v('tile_cols_log2'), ltr=v('tile_rows_log2'), fps=v('header_size_in_bytes'),
                 fhl=(f['header_size_in_bytes'][0] + 16 + 7) // 8, lrf=refidx[0], grf=refidx[1], arf=refidx[2],
                 lsb=bias[0], gsb=bias[1], asb=bias[2],
                 ll=int(v('base_q_idx') == 0 and not any(v(f'delta_q_{n}.delta_coded') for n in ('y_dc', 'uv_dc', 'uv_ac'))))
        se = v('segmentation_enabled')
        um = se and v('segmentation_update_map')
        tu = um and v('segmentation_temporal_update')
        if um:
            tree = [v(f'segmentation_tree_probs[{i}].prob', 255) if v(f'segmentation_tree_probs[{i}].prob_coded')
                    else 255 for i in range(7)]
            pred = [v(f'segmentation_pred_prob[{i}].prob', 255) if tu and v(f'segmentation_pred_prob[{i}].prob_coded')
                    else 255 for i in range(3)]
        if se and v('segmentation_update_data'):
            seg_ref = [(v(f'feature_enabled[{i}][2]'), v(f'feature_value[{i}][2]') if v(f'feature_enabled[{i}][2]')
                        else seg_ref[i][1], v(f'feature_enabled[{i}][3]')) for i in range(8)]
        r.update(se=se, sum=um, stu=tu, tp=tree, pp=pred, sre=[s[0] for s in seg_ref], sr=[s[1] for s in seg_ref],
                 srs=[s[2] for s in seg_ref])
        target = next(i for i in range(1, 64) if i not in slot_surface)
        r['refs'] = list(slot_surface)
        r['target'] = target
        for i in range(8):
            if refresh >> i & 1:
                slot_size[i], slot_surface[i] = (w, h), target
        records.append(r)
    return records


def write_records(path, records):
    lines = []
    for r in records:
        if r is None:
            lines.append('skip')
            continue
        lines.append(' '.join(f'{k}=' + (','.join(map(str, x)) if isinstance(x, list) else str(x)) for k, x in r.items()))
    path.write_text('\n'.join(lines) + '\n')


HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>
#include <VP9DefaultProbs.hpp>
#include <VP9Message.hpp>
extern "C" {
#include "vp9-va.h"
unsigned mesa_ref_message_size(void);
unsigned mesa_ref_probs_segment_size(void);
unsigned mesa_ref_probs_size(void);
void mesa_ref_default_probs(void *);
unsigned mesa_ref_ctx_size(unsigned, unsigned);
void mesa_ref_reset(unsigned);
unsigned mesa_ref_decode(VADecPictureParameterBufferVP9 *, VASliceParameterBufferVP9 *, const void *, unsigned,
                         unsigned, void *, void *);
}
using namespace VP9Message;

static std::vector<std::vector<uint8_t>> readIvf(const char *path) {
    std::ifstream f(path, std::ios::binary);
    std::vector<uint8_t> d((std::istreambuf_iterator<char>(f)), {});
    assert(d.size() >= 32 && !memcmp(d.data(), "DKIF", 4));
    std::vector<std::vector<uint8_t>> out;
    for (size_t o = d[6] | d[7] << 8; o + 12 <= d.size();) {
        uint32_t n = d[o] | d[o + 1] << 8 | d[o + 2] << 16 | uint32_t(d[o + 3]) << 24;
        o += 12;
        assert(o + n <= d.size() && n);
        // The IVF muxer merges hidden frames into superframes; AppleGVA splits them before the driver sees them.
        const uint8_t *pkt = d.data() + o, last = pkt[n - 1];
        size_t count = (last & 7) + 1, mag = ((last >> 3) & 3) + 1, index = 2 + mag * count;
        if ((last & 0xE0) == 0xC0 && n >= index && pkt[n - index] == last) {
            size_t at = 0;
            for (size_t i = 0; i < count; i++) {
                size_t len = 0;
                for (size_t b = 0; b < mag; b++) len |= size_t(pkt[n - index + 1 + i * mag + b]) << (8 * b);
                assert(at + len <= n - index);
                out.emplace_back(pkt + at, pkt + at + len);
                at += len;
            }
        } else {
            out.emplace_back(pkt, pkt + n);
        }
        o += n;
    }
    return out;
}

static std::vector<int> ints(const std::string &s) {
    std::vector<int> v;
    std::stringstream ss(s);
    for (std::string x; std::getline(ss, x, ',');) v.push_back(std::stoi(x));
    return v;
}

static void dump(const char *what, const uint8_t *a, const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (a[i] != b[i]) fprintf(stderr, "  %s +0x%zx: NootedRed %02x, Mesa %02x\n", what, i, a[i], b[i]);
}

static int compareDefaults() {
    assert(mesa_ref_message_size() == sizeof(Message));
    assert(mesa_ref_probs_segment_size() == sizeof(ProbsSegment));
    assert(mesa_ref_probs_size() == sizeof(Probs));
    static uint8_t mesa[sizeof(Probs)], ours[sizeof(Probs)];
    mesa_ref_default_probs(mesa);
    fillDefaultProbs(ours);
    if (memcmp(mesa, ours, sizeof(ours))) { dump("default probs", ours, mesa, sizeof(ours)); return 1; }
    for (unsigned depth : {8u, 10u}) assert(contextSize(depth) == mesa_ref_ctx_size(0x200, depth));
    // Edge cases: never read past the frame, never crash, refuse what the VCN must not see.
    Session s;
    Message m;
    static ProbsSegment p;
    Surface refs[8] = {};
    const uint8_t showExisting[] = {0x88};
    assert(s.build(showExisting, 1, 1, refs, false, m, p) == Status::ShowExisting);
    const uint8_t profile1[] = {0xA0, 0, 0, 0};
    assert(s.build(profile1, 4, 1, refs, false, m, p) == Status::Unsupported);
    const uint8_t badSync[] = {0x82, 0x49, 0x83, 0x43, 0, 0, 0, 0, 0, 0};
    assert(s.build(badSync, sizeof(badSync), 1, refs, false, m, p) == Status::Malformed);
    assert(s.build(showExisting, 0, 1, refs, false, m, p) == Status::Malformed);
    uint32_t seed = 1;
    for (int i = 0; i < 20000; i++) {
        uint8_t buf[24];
        size_t n = 1 + i % sizeof(buf);
        for (auto &b : buf) b = uint8_t((seed = seed * 1103515245 + 12345) >> 16);
        buf[0] = uint8_t(0x80 | (buf[0] & 0x3F));
        std::vector<uint8_t> exact(buf, buf + n);    // heap copy of exactly n bytes: ASan catches overreads
        s.build(exact.data(), n, 1 + i % 9, refs, false, m, p);
    }
    puts("PASS VP9 message: struct sizes, default probabilities, context size, malformed input");
    return 0;
}

static int compareClip(const char *ivf, const char *recordsPath, bool p010) {
    auto frames = readIvf(ivf);
    std::ifstream rf(recordsPath);
    std::vector<std::string> lines;
    for (std::string l; std::getline(rf, l);) lines.push_back(l);
    assert(lines.size() == frames.size());
    mesa_ref_reset(p010 ? 2 : 1);
    Session session;
    static ProbsSegment ours, mesaProbs;
    memset(&ours, 0, sizeof(ours));
    Surface slots[8] = {};
    unsigned decoded = 0, skipped = 0;
    for (size_t n = 0; n < frames.size(); n++) {
        Message msg {}, mesaMsg {};
        if (lines[n] == "skip") {
            assert(session.build(frames[n].data(), frames[n].size(), 99, slots, p010, msg, ours) == Status::ShowExisting);
            skipped++;
            continue;
        }
        std::map<std::string, std::string> r;
        std::stringstream ss(lines[n]);
        for (std::string kv; ss >> kv;) r[kv.substr(0, kv.find('='))] = kv.substr(kv.find('=') + 1);
        auto I = [&](const char *k) { return std::stoi(r.at(k)); };
        VADecPictureParameterBufferVP9 pp {};
        VASliceParameterBufferVP9 sp {};
        pp.frame_width = I("fw"), pp.frame_height = I("fh");
        auto refs = ints(r["refs"]);
        Surface surfaces[8];
        for (int i = 0; i < 8; i++) {
            pp.reference_frames[i] = refs[i] ? refs[i] : VA_INVALID_SURFACE;
            surfaces[i] = refs[i];
        }
        auto &b = pp.pic_fields.bits;
        b.subsampling_x = b.subsampling_y = I("ss");
        b.frame_type = I("ft"), b.show_frame = I("sf"), b.error_resilient_mode = I("er"), b.intra_only = I("io");
        b.allow_high_precision_mv = I("hp"), b.mcomp_filter_type = I("mf"), b.frame_parallel_decoding_mode = I("fp");
        b.reset_frame_context = I("rfc"), b.refresh_frame_context = I("rc"), b.frame_context_idx = I("fci");
        b.segmentation_enabled = I("se"), b.segmentation_temporal_update = I("stu");
        b.segmentation_update_map = I("sum"), b.last_ref_frame = I("lrf"), b.last_ref_frame_sign_bias = I("lsb");
        b.golden_ref_frame = I("grf"), b.golden_ref_frame_sign_bias = I("gsb"), b.alt_ref_frame = I("arf");
        b.alt_ref_frame_sign_bias = I("asb"), b.lossless_flag = I("ll");
        pp.filter_level = I("fl"), pp.sharpness_level = I("sl"), pp.log2_tile_rows = I("ltr");
        pp.log2_tile_columns = I("ltc"), pp.frame_header_length_in_bytes = I("fhl");
        pp.first_partition_size = I("fps"), pp.profile = I("prof"), pp.bit_depth = I("bd");
        auto tp = ints(r["tp"]), pr = ints(r["pp"]), sre = ints(r["sre"]), sr = ints(r["sr"]), srs = ints(r["srs"]);
        for (int i = 0; i < 7; i++) pp.mb_segment_tree_probs[i] = tp[i];
        for (int i = 0; i < 3; i++) pp.segment_pred_probs[i] = pr[i];
        sp.slice_data_size = frames[n].size();
        for (int i = 0; i < 8; i++) {
            sp.seg_param[i].segment_flags.fields.segment_reference_enabled = sre[i];
            sp.seg_param[i].segment_flags.fields.segment_reference = sr[i];
            sp.seg_param[i].segment_flags.fields.segment_reference_skipped = srs[i];
        }
        unsigned size = mesa_ref_decode(&pp, &sp, frames[n].data(), frames[n].size(), I("target"), &mesaMsg, &mesaProbs);
        Header h;
        Status st = session.build(frames[n].data(), frames[n].size(), I("target"), surfaces, p010, msg, ours, &h);
        if (st != Status::Ok || size != sizeof(Message)) {
            fprintf(stderr, "%s frame %zu: status %d, Mesa size %u\n", ivf, n, int(st), size);
            return 1;
        }
        if (h.width != pp.frame_width || h.height != pp.frame_height || h.uncompressedHeaderSize != pp.frame_header_length_in_bytes ||
            h.compressedHeaderSize != pp.first_partition_size) {
            fprintf(stderr, "%s frame %zu: header %ux%u %u/%u, CBS %ux%u %u/%u\n", ivf, n, h.width, h.height,
                    h.uncompressedHeaderSize, h.compressedHeaderSize, pp.frame_width, pp.frame_height,
                    pp.frame_header_length_in_bytes, pp.first_partition_size);
            return 1;
        }
        if (memcmp(&msg, &mesaMsg, sizeof(msg)) || memcmp(&ours, &mesaProbs, sizeof(ours))) {
            fprintf(stderr, "%s frame %zu differs:\n", ivf, n);
            dump("message", (uint8_t *)&msg, (uint8_t *)&mesaMsg, sizeof(msg));
            dump("probs", (uint8_t *)&ours, (uint8_t *)&mesaProbs, sizeof(ours));
            return 1;
        }
        for (int i = 0; i < 8; i++) slots[i] = surfaces[i];
        decoded++;
    }
    printf("PASS %s: %u frames identical to Mesa, %u show-existing skipped\n", ivf, decoded, skipped);
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "defaults")) return compareDefaults();
    if (argc == 2 && !strcmp(argv[1], "dump-probs")) {
        static uint8_t probs[sizeof(Probs)];
        mesa_ref_default_probs(probs);
        fwrite(probs, 1, sizeof(probs), stdout);
        return 0;
    }
    assert(argc == 5);
    return compareClip(argv[2], argv[3], argv[4][0] == '1');
}
'''


def write_probs_header(exe):
    data = subprocess.run([str(exe), 'dump-probs'], capture_output=True, check=True).stdout
    rows = [', '.join(f'0x{b:02x}' for b in data[i:i + 16]) for i in range(0, len(data), 16)]
    PROBS_HPP.write_text(
        '//! VP9 plan, phase V2: default probability tables the VCN expects in a new VP9 session context\n'
        '//! (ac_vcn_vp9_fill_probs_table). Generated by `python3 tools/test_vp9_message.py --write-probs` from Mesa\n'
        f'//! {COMMIT} (src/amd/common/ac_vcn_vp9_default.h).\n'
        '//! Copyright 2018 Advanced Micro Devices, Inc. All Rights Reserved. SPDX-License-Identifier: MIT\n'
        '//! Same MIT terms as the notice in VP9Message.hpp: the copyright and permission notices must be kept.\n'
        '#pragma once\n#include <VP9Message.hpp>\n\nnamespace VP9Message {\n\n'
        f'inline constexpr uint8_t DefaultProbs[sizeof(Probs)] = {{\n    ' + ',\n    '.join(rows) + ',\n};\n\n'
        '//! Fills the probability part of the hardware context once per session, before the first frame.\n'
        'inline void fillDefaultProbs(void *hwContext) {\n'
        '    auto *out = static_cast<uint8_t *>(hwContext);\n'
        '    for (size_t i = 0; i < sizeof(DefaultProbs); i++) { out[i] = DefaultProbs[i]; }\n'
        '}\n\n}    // namespace VP9Message\n')


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    obj = build_reference()
    if not PROBS_HPP.exists():
        PROBS_HPP.write_text('#pragma once\n#include <VP9Message.hpp>\nnamespace VP9Message {\n'
                             'inline constexpr uint8_t DefaultProbs[sizeof(Probs)] = {};\n'
                             'inline void fillDefaultProbs(void *) {}\n}\n')
    source = WORK / 'harness.cpp'
    source.write_text(HARNESS)
    exe = WORK / 'harness'
    subprocess.run([shutil.which('clang++') or 'clang++', '-std=c++20', '-O1', '-g', '-Wall', '-Wextra',
                    '-Wno-missing-field-initializers', '-fsanitize=address,undefined',
                    '-I' + str(ROOT / 'NootedRed'), '-I' + str(ROOT / 'tools/fixtures'), str(source), str(obj),
                    '-o', str(exe)], check=True, timeout=180)
    if '--write-probs' in sys.argv:
        write_probs_header(exe)
        subprocess.run([shutil.which('clang++') or 'clang++', '-std=c++20', '-O1', '-g',
                        '-fsanitize=address,undefined', '-I' + str(ROOT / 'NootedRed'),
                        '-I' + str(ROOT / 'tools/fixtures'), str(source), str(obj), '-o', str(exe)],
                       check=True, timeout=180)
    subprocess.run([str(exe), 'defaults'], check=True, timeout=120)
    for name, size, frames, options, pix_fmt, p010 in CLIPS:
        ivf = make_clip(name, size, frames, options, pix_fmt)
        records = WORK / f'{name}.records'
        write_records(records, va_records(trace(ivf)))
        subprocess.run([str(exe), 'clip', str(ivf), str(records), '1' if p010 else '0'], check=True, timeout=120)
    print('PASS VP9 message: NootedRed builds the same VCN messages as Mesa on every clip')


if __name__ == '__main__':
    main()
