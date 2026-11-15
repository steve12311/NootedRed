/* Field names of libva va_dec_vp9.h, for tools/test_vp9_message.py (shared by the C reference and the C++ harness). */
#pragma once
#include <stdint.h>

typedef unsigned VASurfaceID;
#define VA_INVALID_SURFACE 0xffffffffu
enum { VA_SLICE_DATA_FLAG_ALL, VA_SLICE_DATA_FLAG_BEGIN, VA_SLICE_DATA_FLAG_MIDDLE, VA_SLICE_DATA_FLAG_END };
typedef struct {
   uint16_t frame_width, frame_height;
   VASurfaceID reference_frames[8];
   union {
      struct {
         uint32_t subsampling_x : 1, subsampling_y : 1, frame_type : 1, show_frame : 1, error_resilient_mode : 1,
            intra_only : 1, allow_high_precision_mv : 1, mcomp_filter_type : 3, frame_parallel_decoding_mode : 1,
            reset_frame_context : 2, refresh_frame_context : 1, frame_context_idx : 2, segmentation_enabled : 1,
            segmentation_temporal_update : 1, segmentation_update_map : 1, last_ref_frame : 3,
            last_ref_frame_sign_bias : 1, golden_ref_frame : 3, golden_ref_frame_sign_bias : 1, alt_ref_frame : 3,
            alt_ref_frame_sign_bias : 1, lossless_flag : 1;
      } bits;
      uint32_t value;
   } pic_fields;
   uint8_t filter_level, sharpness_level, log2_tile_rows, log2_tile_columns, frame_header_length_in_bytes;
   uint16_t first_partition_size;
   uint8_t mb_segment_tree_probs[7], segment_pred_probs[3], profile, bit_depth;
} VADecPictureParameterBufferVP9;
typedef struct {
   union {
      struct { uint16_t segment_reference_enabled : 1, segment_reference : 2, segment_reference_skipped : 1; } fields;
      uint16_t value;
   } segment_flags;
   uint8_t filter_level[4][2];
   int16_t luma_ac_quant_scale, luma_dc_quant_scale, chroma_ac_quant_scale, chroma_dc_quant_scale;
} VASegmentParameterVP9;
typedef struct {
   uint32_t slice_data_size, slice_data_offset, slice_data_flag;
   VASegmentParameterVP9 seg_param[8];
} VASliceParameterBufferVP9;

