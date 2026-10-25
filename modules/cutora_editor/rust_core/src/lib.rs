//! CUTORA Native Rust High-Performance Engine (`cutora_rust_core.dll`)
//!
//! Provides deterministic, zero-GC-overhead native capabilities for low-end / GPU-less PCs:
//! 1. Hard-capped LRU Frame & ROI Arena (default 384 MB hard cap, instant `Drop` on eviction).
//! 2. Vectorized Streaming Audio Waveform Min/Max Peak & RMS Scanner.
//! 3. Multi-threaded O(1) Separable 3-Pass Gaussian Box Blur + 4-Band Chromatic Glass Shader.
//! 4. Fast Sequence Similarity Scorer for Smart Stitch anchor alignment.
//! 5. Direct Win32 Kernel Working-Set Governor (`K32EmptyWorkingSet` + `SetProcessWorkingSetSizeEx`).

use std::collections::VecDeque;
use std::slice;
use std::sync::{Mutex, OnceLock};
use std::thread;

// ─────────────────────────────────────────────────────────────────────────────
// 1. Hard-Capped Native Memory Arena (Deterministic RAII / Drop)
// ─────────────────────────────────────────────────────────────────────────────

struct ArenaEntry {
    key: u64,
    width: u32,
    height: u32,
    data: Vec<u8>,
}

struct HardCappedArena {
    max_bytes: usize,
    used_bytes: usize,
    entries: VecDeque<ArenaEntry>,
}

impl HardCappedArena {
    fn new(max_bytes: usize) -> Self {
        Self {
            max_bytes,
            used_bytes: 0,
            entries: VecDeque::with_capacity(64),
        }
    }

    fn set_limit(&mut self, max_bytes: usize) {
        self.max_bytes = max_bytes.max(16 * 1024 * 1024);
        self.evict_until_within_limit(0);
    }

    fn evict_until_within_limit(&mut self, needed: usize) {
        while !self.entries.is_empty() && self.used_bytes.saturating_add(needed) > self.max_bytes {
            if let Some(old) = self.entries.pop_front() {
                self.used_bytes = self.used_bytes.saturating_sub(old.data.len());
                drop(old); // Deterministic immediate deallocation
            }
        }
    }

    fn put(&mut self, key: u64, slice_data: &[u8], width: u32, height: u32) -> bool {
        let len = slice_data.len();
        if len == 0 || len > self.max_bytes {
            return false;
        }
        if let Some(pos) = self.entries.iter().position(|e| e.key == key) {
            if let Some(old) = self.entries.remove(pos) {
                self.used_bytes = self.used_bytes.saturating_sub(old.data.len());
            }
        }
        self.evict_until_within_limit(len);
        let mut buf = Vec::with_capacity(len);
        buf.extend_from_slice(slice_data);
        self.used_bytes += len;
        self.entries.push_back(ArenaEntry {
            key,
            width,
            height,
            data: buf,
        });
        true
    }

    fn get(&mut self, key: u64, out_buf: &mut [u8]) -> Option<(usize, u32, u32)> {
        let pos = self.entries.iter().position(|e| e.key == key)?;
        let mut entry = self.entries.remove(pos)?;
        let len = entry.data.len();
        let w = entry.width;
        let h = entry.height;
        if out_buf.len() >= len {
            out_buf[..len].copy_from_slice(&entry.data);
            self.entries.push_back(entry);
            Some((len, w, h))
        } else {
            entry.data.shrink_to_fit();
            self.entries.push_back(entry);
            None
        }
    }

    fn clear(&mut self) -> usize {
        let freed = self.used_bytes;
        self.entries.clear();
        self.entries.shrink_to_fit();
        self.used_bytes = 0;
        freed
    }
}

static GLOBAL_ARENA: OnceLock<Mutex<HardCappedArena>> = OnceLock::new();

fn get_arena() -> &'static Mutex<HardCappedArena> {
    GLOBAL_ARENA.get_or_init(|| Mutex::new(HardCappedArena::new(384 * 1024 * 1024)))
}

#[no_mangle]
pub extern "C" fn cutora_rust_version() -> u32 {
    30000
}

#[no_mangle]
pub extern "C" fn cutora_rust_arena_set_limit(max_bytes: usize) {
    if let Ok(mut arena) = get_arena().lock() {
        arena.set_limit(max_bytes);
    }
}

#[no_mangle]
pub extern "C" fn cutora_rust_arena_used_bytes() -> usize {
    if let Ok(arena) = get_arena().lock() {
        arena.used_bytes
    } else {
        0
    }
}

#[no_mangle]
pub extern "C" fn cutora_rust_arena_clear() -> usize {
    if let Ok(mut arena) = get_arena().lock() {
        arena.clear()
    } else {
        0
    }
}

#[no_mangle]
pub unsafe extern "C" fn cutora_rust_arena_put(
    key: u64,
    data: *const u8,
    len: usize,
    width: u32,
    height: u32,
) -> i32 {
    if data.is_null() || len == 0 {
        return 0;
    }
    let slice_data = slice::from_raw_parts(data, len);
    if let Ok(mut arena) = get_arena().lock() {
        if arena.put(key, slice_data, width, height) {
            return 1;
        }
    }
    0
}

#[no_mangle]
pub unsafe extern "C" fn cutora_rust_arena_get(
    key: u64,
    out_buf: *mut u8,
    max_len: usize,
    out_w: *mut u32,
    out_h: *mut u32,
) -> usize {
    if out_buf.is_null() || max_len == 0 {
        return 0;
    }
    let out_slice = slice::from_raw_parts_mut(out_buf, max_len);
    if let Ok(mut arena) = get_arena().lock() {
        if let Some((len, w, h)) = arena.get(key, out_slice) {
            if !out_w.is_null() {
                *out_w = w;
            }
            if !out_h.is_null() {
                *out_h = h;
            }
            return len;
        }
    }
    0
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. Streaming Vectorized Audio Waveform Peak & Energy Scanner
// ─────────────────────────────────────────────────────────────────────────────

#[no_mangle]
pub unsafe extern "C" fn cutora_rust_waveform_peaks_f32(
    samples: *const f32,
    num_samples: usize,
    bucket_size: usize,
    out_peaks: *mut f32,
    max_buckets: usize,
) -> usize {
    if samples.is_null() || out_peaks.is_null() || num_samples == 0 || bucket_size == 0 || max_buckets == 0 {
        return 0;
    }
    let src = slice::from_raw_parts(samples, num_samples);
    let num_buckets = (num_samples / bucket_size).min(max_buckets);
    let dst = slice::from_raw_parts_mut(out_peaks, num_buckets * 2);

    for b in 0..num_buckets {
        let chunk = &src[b * bucket_size..(b + 1) * bucket_size];
        let mut lo = f32::INFINITY;
        let mut hi = f32::NEG_INFINITY;
        for &v in chunk {
            let val = if v.is_finite() { v } else { 0.0 };
            if val < lo {
                lo = val;
            }
            if val > hi {
                hi = val;
            }
        }
        dst[b * 2] = lo.clamp(-1.0, 1.0);
        dst[b * 2 + 1] = hi.clamp(-1.0, 1.0);
    }
    num_buckets
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. Multi-Core Separable O(1) Gaussian Box Blur & Chromatic Glass Shader
// ─────────────────────────────────────────────────────────────────────────────

fn box_blur_h_bgra(src: &[u8], dst: &mut [u8], w: usize, h: usize, radius: usize) {
    if radius == 0 || w == 0 || h == 0 {
        dst.copy_from_slice(src);
        return;
    }
    let r = radius.min(w.saturating_sub(1) / 2).max(1);
    let diam = (r * 2 + 1) as u32;
    let row_bytes = w * 4;

    let n_threads = thread::available_parallelism().map(|n| n.get()).unwrap_or(4).min(8).min(h.max(1));
    let rows_per_thread = (h + n_threads - 1) / n_threads;

    thread::scope(|s| {
        for (chunk_idx, dst_chunk) in dst.chunks_mut(rows_per_thread * row_bytes).enumerate() {
            let y_start = chunk_idx * rows_per_thread;
            let chunk_h = dst_chunk.len() / row_bytes;
            let src_chunk = &src[y_start * row_bytes..(y_start + chunk_h) * row_bytes];
            s.spawn(move || {
                for y in 0..chunk_h {
                    let row_in = &src_chunk[y * row_bytes..(y + 1) * row_bytes];
                    let row_out = &mut dst_chunk[y * row_bytes..(y + 1) * row_bytes];

                    let mut sum_b: u32 = (row_in[0] as u32) * (r as u32 + 1);
                    let mut sum_g: u32 = (row_in[1] as u32) * (r as u32 + 1);
                    let mut sum_r: u32 = (row_in[2] as u32) * (r as u32 + 1);
                    let mut sum_a: u32 = (row_in[3] as u32) * (r as u32 + 1);

                    for i in 1..=r {
                        let ix = i.min(w - 1) * 4;
                        sum_b += row_in[ix] as u32;
                        sum_g += row_in[ix + 1] as u32;
                        sum_r += row_in[ix + 2] as u32;
                        sum_a += row_in[ix + 3] as u32;
                    }

                    for x in 0..w {
                        let out_ix = x * 4;
                        row_out[out_ix] = (sum_b / diam) as u8;
                        row_out[out_ix + 1] = (sum_g / diam) as u8;
                        row_out[out_ix + 2] = (sum_r / diam) as u8;
                        row_out[out_ix + 3] = (sum_a / diam) as u8;

                        let left_x = x.saturating_sub(r) * 4;
                        let right_x = (x + r + 1).min(w - 1) * 4;

                        sum_b = sum_b + (row_in[right_x] as u32) - (row_in[left_x] as u32);
                        sum_g = sum_g + (row_in[right_x + 1] as u32) - (row_in[left_x + 1] as u32);
                        sum_r = sum_r + (row_in[right_x + 2] as u32) - (row_in[left_x + 2] as u32);
                        sum_a = sum_a + (row_in[right_x + 3] as u32) - (row_in[left_x + 3] as u32);
                    }
                }
            });
        }
    });
}

fn box_blur_v_bgra(src: &[u8], dst: &mut [u8], w: usize, h: usize, radius: usize) {
    if radius == 0 || w == 0 || h == 0 {
        dst.copy_from_slice(src);
        return;
    }
    let r = radius.min(h.saturating_sub(1) / 2).max(1);
    let diam = (r * 2 + 1) as u32;
    let stride = w * 4;

    for x in 0..w {
        let col_off = x * 4;
        let mut sum_b: u32 = (src[col_off] as u32) * (r as u32 + 1);
        let mut sum_g: u32 = (src[col_off + 1] as u32) * (r as u32 + 1);
        let mut sum_r: u32 = (src[col_off + 2] as u32) * (r as u32 + 1);
        let mut sum_a: u32 = (src[col_off + 3] as u32) * (r as u32 + 1);

        for i in 1..=r {
            let iy = i.min(h - 1) * stride + col_off;
            sum_b += src[iy] as u32;
            sum_g += src[iy + 1] as u32;
            sum_r += src[iy + 2] as u32;
            sum_a += src[iy + 3] as u32;
        }

        for y in 0..h {
            let out_ix = y * stride + col_off;
            dst[out_ix] = (sum_b / diam) as u8;
            dst[out_ix + 1] = (sum_g / diam) as u8;
            dst[out_ix + 2] = (sum_r / diam) as u8;
            dst[out_ix + 3] = (sum_a / diam) as u8;

            let top_y = y.saturating_sub(r) * stride + col_off;
            let bot_y = (y + r + 1).min(h - 1) * stride + col_off;

            sum_b = sum_b + (src[bot_y] as u32) - (src[top_y] as u32);
            sum_g = sum_g + (src[bot_y + 1] as u32) - (src[top_y + 1] as u32);
            sum_r = sum_r + (src[bot_y + 2] as u32) - (src[top_y + 2] as u32);
            sum_a = sum_a + (src[bot_y + 3] as u32) - (src[top_y + 3] as u32);
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn cutora_rust_blur_bgra(
    src_ptr: *const u8,
    dst_ptr: *mut u8,
    width: usize,
    height: usize,
    radius: usize,
    passes: usize,
) -> i32 {
    let total = width.saturating_mul(height).saturating_mul(4);
    if src_ptr.is_null() || dst_ptr.is_null() || total == 0 {
        return 0;
    }
    let src = slice::from_raw_parts(src_ptr, total);
    let dst = slice::from_raw_parts_mut(dst_ptr, total);
    let r = radius.max(1);
    let n_passes = passes.clamp(1, 3);

    let mut tmp = vec![0u8; total];
    box_blur_h_bgra(src, &mut tmp, width, height, r);
    box_blur_v_bgra(&tmp, dst, width, height, r);

    for _ in 1..n_passes {
        box_blur_h_bgra(dst, &mut tmp, width, height, r);
        box_blur_v_bgra(&tmp, dst, width, height, r);
    }
    1
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. Fast Token/Character Sequence Similarity for Smart Stitch
// ─────────────────────────────────────────────────────────────────────────────

#[no_mangle]
pub unsafe extern "C" fn cutora_rust_lcs_ratio_u32(
    a_ptr: *const u32,
    a_len: usize,
    b_ptr: *const u32,
    b_len: usize,
) -> f32 {
    if a_ptr.is_null() || b_ptr.is_null() || a_len == 0 || b_len == 0 {
        return 0.0;
    }
    let a = slice::from_raw_parts(a_ptr, a_len.min(4096));
    let b = slice::from_raw_parts(b_ptr, b_len.min(4096));
    let mut prev = vec![0u16; b.len() + 1];
    let mut curr = vec![0u16; b.len() + 1];

    for &ca in a {
        for (j, &cb) in b.iter().enumerate() {
            if ca == cb {
                curr[j + 1] = prev[j].saturating_add(1);
            } else {
                curr[j + 1] = prev[j + 1].max(curr[j]);
            }
        }
        prev.copy_from_slice(&curr);
    }
    let lcs = prev[b.len()] as f32;
    (2.0 * lcs) / ((a.len() + b.len()) as f32)
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. Windows Kernel Working-Set & Physical Memory Watchdog
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(target_os = "windows")]
mod win32 {
    #[repr(C)]
    pub struct MemoryStatusEx {
        pub dw_length: u32,
        pub dw_memory_load: u32,
        pub ull_total_phys: u64,
        pub ull_avail_phys: u64,
        pub ull_total_page_file: u64,
        pub ull_avail_page_file: u64,
        pub ull_total_virtual: u64,
        pub ull_avail_virtual: u64,
        pub ull_avail_extended_virtual: u64,
    }

    #[link(name = "kernel32")]
    extern "system" {
        pub fn GetCurrentProcess() -> isize;
        pub fn K32EmptyWorkingSet(h_process: isize) -> i32;
        pub fn SetProcessWorkingSetSizeEx(
            h_process: isize,
            dw_minimum_working_set_size: usize,
            dw_maximum_working_set_size: usize,
            flags: u32,
        ) -> i32;
        pub fn GlobalMemoryStatusEx(lp_buffer: *mut MemoryStatusEx) -> i32;
    }
}

#[no_mangle]
pub extern "C" fn cutora_rust_avail_phys_mb() -> u64 {
    #[cfg(target_os = "windows")]
    unsafe {
        let mut st = win32::MemoryStatusEx {
            dw_length: std::mem::size_of::<win32::MemoryStatusEx>() as u32,
            dw_memory_load: 0,
            ull_total_phys: 0,
            ull_avail_phys: 0,
            ull_total_page_file: 0,
            ull_avail_page_file: 0,
            ull_total_virtual: 0,
            ull_avail_virtual: 0,
            ull_avail_extended_virtual: 0,
        };
        if win32::GlobalMemoryStatusEx(&mut st) != 0 {
            return st.ull_avail_phys / (1024 * 1024);
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cutora_rust_total_phys_mb() -> u64 {
    #[cfg(target_os = "windows")]
    unsafe {
        let mut st = win32::MemoryStatusEx {
            dw_length: std::mem::size_of::<win32::MemoryStatusEx>() as u32,
            dw_memory_load: 0,
            ull_total_phys: 0,
            ull_avail_phys: 0,
            ull_total_page_file: 0,
            ull_avail_page_file: 0,
            ull_total_virtual: 0,
            ull_avail_virtual: 0,
            ull_avail_extended_virtual: 0,
        };
        if win32::GlobalMemoryStatusEx(&mut st) != 0 {
            return st.ull_total_phys / (1024 * 1024);
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cutora_rust_trim_working_set() -> u64 {
    let before = cutora_rust_avail_phys_mb();
    // 1. Clear our own Rust arena if system RAM is under pressure (< 4 GB free)
    if before > 0 && before < 4096 {
        let _ = cutora_rust_arena_clear();
    }
    // 2. Ask Windows kernel to immediately flush unused working set pages back to available RAM
    #[cfg(target_os = "windows")]
    unsafe {
        let hproc = win32::GetCurrentProcess();
        let _ = win32::K32EmptyWorkingSet(hproc);
        let _ = win32::SetProcessWorkingSetSizeEx(hproc, usize::MAX, usize::MAX, 0);
    }
    cutora_rust_avail_phys_mb()
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. Native Export Pixel Engine: Fused BGR888 -> RGB32 Resize & Masked Blit
// ─────────────────────────────────────────────────────────────────────────────

#[no_mangle]
pub extern "C" fn cutora_rust_bgr_to_rgb32_resize(
    src_ptr: *const u8,
    src_w: u32,
    src_h: u32,
    src_stride: u32,
    dst_ptr: *mut u8,
    dst_w: u32,
    dst_h: u32,
    dst_stride: u32,
) -> i32 {
    if src_ptr.is_null() || dst_ptr.is_null() || src_w == 0 || src_h == 0 || dst_w == 0 || dst_h == 0 {
        return -1;
    }
    let sw = src_w as usize;
    let sh = src_h as usize;
    let s_stride = src_stride as usize;
    let dw = dst_w as usize;
    let dh = dst_h as usize;
    let d_stride = dst_stride as usize;

    if s_stride < sw * 3 || d_stride < dw * 4 {
        return -2;
    }

    let src_total = (sh - 1) * s_stride + sw * 3;
    let dst_total = (dh - 1) * d_stride + dw * 4;
    let src = unsafe { slice::from_raw_parts(src_ptr, src_total) };
    let dst = unsafe { slice::from_raw_parts_mut(dst_ptr, dst_total) };

    if sw == dw && sh == dh {
        // Fast 1:1 3-channel BGR -> 4-channel BGRX (Format_RGB32) conversion using 64-bit word loads/stores
        for y in 0..dh {
            unsafe {
                let s_row = src_ptr.add(y * s_stride);
                let d_row = dst_ptr.add(y * d_stride);
                let mut x = 0usize;
                while x + 4 <= dw {
                    let sp = s_row.add(x * 3);
                    let dp = d_row.add(x * 4) as *mut u32;
                    let w0 = std::ptr::read_unaligned(sp as *const u64);
                    let w1 = std::ptr::read_unaligned(sp.add(6) as *const u64);

                    let p0 = ((w0 as u32) & 0x00FF_FFFF) | 0xFF00_0000;
                    let p1 = (((w0 >> 24) as u32) & 0x00FF_FFFF) | 0xFF00_0000;
                    let p2 = ((w1 as u32) & 0x00FF_FFFF) | 0xFF00_0000;
                    let p3 = (((w1 >> 24) as u32) & 0x00FF_FFFF) | 0xFF00_0000;

                    std::ptr::write_unaligned(dp, p0);
                    std::ptr::write_unaligned(dp.add(1), p1);
                    std::ptr::write_unaligned(dp.add(2), p2);
                    std::ptr::write_unaligned(dp.add(3), p3);
                    x += 4;
                }
                while x < dw {
                    let sp = s_row.add(x * 3);
                    let dp = d_row.add(x * 4);
                    *dp = *sp;
                    *dp.add(1) = *sp.add(1);
                    *dp.add(2) = *sp.add(2);
                    *dp.add(3) = 255;
                    x += 1;
                }
            }
        }
        return 0;
    }

    // Fused 1-pass fixed-point bilinear resize (BGR888 -> Format_RGB32)
    // Matches OpenCV INTER_LINEAR center convention: fx = (dx + 0.5) * (sw / dw) - 0.5
    let scale_x = (sw as f32) / (dw as f32);
    let scale_y = (sh as f32) / (dh as f32);

    let mut x_idx0 = Vec::with_capacity(dw);
    let mut x_idx1 = Vec::with_capacity(dw);
    let mut x_w1 = Vec::with_capacity(dw);
    for dx in 0..dw {
        let fx = ((dx as f32) + 0.5) * scale_x - 0.5;
        let x0 = if fx < 0.0 { 0usize } else { (fx as usize).min(sw - 1) };
        let x1 = (x0 + 1).min(sw - 1);
        let frac = if fx < 0.0 { 0.0 } else { (fx - (x0 as f32)).clamp(0.0, 1.0) };
        let w1 = (frac * 256.0 + 0.5) as u32;
        x_idx0.push(x0 * 3);
        x_idx1.push(x1 * 3);
        x_w1.push(w1);
    }

    for dy in 0..dh {
        let fy = ((dy as f32) + 0.5) * scale_y - 0.5;
        let y0 = if fy < 0.0 { 0usize } else { (fy as usize).min(sh - 1) };
        let y1 = (y0 + 1).min(sh - 1);
        let frac_y = if fy < 0.0 { 0.0 } else { (fy - (y0 as f32)).clamp(0.0, 1.0) };
        let wy1 = (frac_y * 256.0 + 0.5) as u32;
        let wy0 = 256 - wy1;

        let s_row0 = &src[y0 * s_stride..y0 * s_stride + sw * 3];
        let s_row1 = &src[y1 * s_stride..y1 * s_stride + sw * 3];
        let d_row = &mut dst[dy * d_stride..dy * d_stride + dw * 4];

        for dx in 0..dw {
            unsafe {
                let i0 = *x_idx0.get_unchecked(dx);
                let i1 = *x_idx1.get_unchecked(dx);
                let wx1 = *x_w1.get_unchecked(dx);
                let wx0 = 256 - wx1;

                let sp00 = s_row0.as_ptr().add(i0);
                let sp01 = s_row0.as_ptr().add(i1);
                let sp10 = s_row1.as_ptr().add(i0);
                let sp11 = s_row1.as_ptr().add(i1);

                let b0 = (*sp00 as u32 * wx0 + *sp01 as u32 * wx1) * wy0
                    + (*sp10 as u32 * wx0 + *sp11 as u32 * wx1) * wy1;
                let g0 = (*sp00.add(1) as u32 * wx0 + *sp01.add(1) as u32 * wx1) * wy0
                    + (*sp10.add(1) as u32 * wx0 + *sp11.add(1) as u32 * wx1) * wy1;
                let r0 = (*sp00.add(2) as u32 * wx0 + *sp01.add(2) as u32 * wx1) * wy0
                    + (*sp10.add(2) as u32 * wx0 + *sp11.add(2) as u32 * wx1) * wy1;

                let b = (b0 + 32768) >> 16;
                let g = (g0 + 32768) >> 16;
                let r = (r0 + 32768) >> 16;
                let pixel = b | (g << 8) | (r << 16) | 0xFF00_0000;
                (d_row.as_mut_ptr().add(dx * 4) as *mut u32).write_unaligned(pixel);
            }
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cutora_rust_blit_solid_rect_copy(
    src_ptr: *const u8,
    src_w: u32,
    src_h: u32,
    src_stride: u32,
    dst_ptr: *mut u8,
    dst_w: u32,
    dst_h: u32,
    dst_stride: u32,
    rx: i32,
    ry: i32,
    rw: i32,
    rh: i32,
    ox: i32,
    oy: i32,
) -> i32 {
    if src_ptr.is_null() || dst_ptr.is_null() || rw <= 0 || rh <= 0 {
        return -1;
    }
    let sw = src_w as i32;
    let sh = src_h as i32;
    let dw = dst_w as i32;
    let dh = dst_h as i32;

    let mut x0 = rx.max(0);
    let mut y0 = ry.max(0);
    let mut x1 = (rx + rw).min(sw);
    let mut y1 = (ry + rh).min(sh);

    if ox + x0 < 0 {
        x0 = -ox;
    }
    if oy + y0 < 0 {
        y0 = -oy;
    }
    if ox + x1 > dw {
        x1 = dw - ox;
    }
    if oy + y1 > dh {
        y1 = dh - oy;
    }
    if x1 <= x0 || y1 <= y0 {
        return 0;
    }

    let s_stride = src_stride as usize;
    let d_stride = dst_stride as usize;
    let row_bytes = ((x1 - x0) as usize) * 4;

    for sy in y0..y1 {
        let dy = (oy + sy) as usize;
        let s_off = (sy as usize) * s_stride + (x0 as usize) * 4;
        let d_off = dy * d_stride + ((ox + x0) as usize) * 4;
        unsafe {
            std::ptr::copy_nonoverlapping(src_ptr.add(s_off), dst_ptr.add(d_off), row_bytes);
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cutora_rust_blit_masked_band_plus(
    src_ptr: *const u8,
    src_w: u32,
    src_h: u32,
    src_stride: u32,
    mask_ptr: *const u8,
    mask_stride: u32,
    dst_ptr: *mut u8,
    dst_w: u32,
    dst_h: u32,
    dst_stride: u32,
    rx: i32,
    ry: i32,
    rw: i32,
    rh: i32,
    ox: i32,
    oy: i32,
) -> i32 {
    if src_ptr.is_null() || dst_ptr.is_null() || rw <= 0 || rh <= 0 {
        return -1;
    }
    let sw = src_w as i32;
    let sh = src_h as i32;
    let dw = dst_w as i32;
    let dh = dst_h as i32;

    let mut x0 = rx.max(0);
    let mut y0 = ry.max(0);
    let mut x1 = (rx + rw).min(sw);
    let mut y1 = (ry + rh).min(sh);

    if ox + x0 < 0 {
        x0 = -ox;
    }
    if oy + y0 < 0 {
        y0 = -oy;
    }
    if ox + x1 > dw {
        x1 = dw - ox;
    }
    if oy + y1 > dh {
        y1 = dh - oy;
    }
    if x1 <= x0 || y1 <= y0 {
        return 0;
    }

    let s_stride = src_stride as usize;
    let d_stride = dst_stride as usize;
    let span_w = (x1 - x0) as usize;

    if mask_ptr.is_null() {
        for sy in y0..y1 {
            let dy = (oy + sy) as usize;
            let s_off = (sy as usize) * s_stride + (x0 as usize) * 4;
            let d_off = dy * d_stride + ((ox + x0) as usize) * 4;
            unsafe {
                let s_row = slice::from_raw_parts(src_ptr.add(s_off), span_w * 4);
                let d_row = slice::from_raw_parts_mut(dst_ptr.add(d_off), span_w * 4);
                for x in 0..span_w {
                    let i = x * 4;
                    if *s_row.get_unchecked(i + 3) == 0 {
                        continue;
                    }
                    *d_row.get_unchecked_mut(i) = (*d_row.get_unchecked(i)).saturating_add(*s_row.get_unchecked(i));
                    *d_row.get_unchecked_mut(i + 1) = (*d_row.get_unchecked(i + 1)).saturating_add(*s_row.get_unchecked(i + 1));
                    *d_row.get_unchecked_mut(i + 2) = (*d_row.get_unchecked(i + 2)).saturating_add(*s_row.get_unchecked(i + 2));
                    *d_row.get_unchecked_mut(i + 3) = (*d_row.get_unchecked(i + 3)).saturating_add(*s_row.get_unchecked(i + 3));
                }
            }
        }
        return 0;
    }

    let m_stride = mask_stride as usize;
    for sy in y0..y1 {
        let dy = (oy + sy) as usize;
        let s_off = (sy as usize) * s_stride + (x0 as usize) * 4;
        let m_off = (sy as usize) * m_stride + (x0 as usize) * 4;
        let d_off = dy * d_stride + ((ox + x0) as usize) * 4;

        unsafe {
            let s_row = slice::from_raw_parts(src_ptr.add(s_off), span_w * 4);
            let m_row = slice::from_raw_parts(mask_ptr.add(m_off), span_w * 4);
            let d_row = slice::from_raw_parts_mut(dst_ptr.add(d_off), span_w * 4);

            for x in 0..span_w {
                let i = x * 4;
                let ma = *m_row.get_unchecked(i + 3) as u32;
                if ma == 0 {
                    continue;
                }
                if ma == 255 {
                    *d_row.get_unchecked_mut(i) = (*d_row.get_unchecked(i)).saturating_add(*s_row.get_unchecked(i));
                    *d_row.get_unchecked_mut(i + 1) = (*d_row.get_unchecked(i + 1)).saturating_add(*s_row.get_unchecked(i + 1));
                    *d_row.get_unchecked_mut(i + 2) = (*d_row.get_unchecked(i + 2)).saturating_add(*s_row.get_unchecked(i + 2));
                    *d_row.get_unchecked_mut(i + 3) = (*d_row.get_unchecked(i + 3)).saturating_add(*s_row.get_unchecked(i + 3));
                } else {
                    let sb = (*s_row.get_unchecked(i) as u32 * ma + 127) / 255;
                    let sg = (*s_row.get_unchecked(i + 1) as u32 * ma + 127) / 255;
                    let sr = (*s_row.get_unchecked(i + 2) as u32 * ma + 127) / 255;
                    let sa = (*s_row.get_unchecked(i + 3) as u32 * ma + 127) / 255;

                    *d_row.get_unchecked_mut(i) = (*d_row.get_unchecked(i)).saturating_add(sb as u8);
                    *d_row.get_unchecked_mut(i + 1) = (*d_row.get_unchecked(i + 1)).saturating_add(sg as u8);
                    *d_row.get_unchecked_mut(i + 2) = (*d_row.get_unchecked(i + 2)).saturating_add(sr as u8);
                    *d_row.get_unchecked_mut(i + 3) = (*d_row.get_unchecked(i + 3)).saturating_add(sa as u8);
                }
            }
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cutora_rust_zero_rect(
    dst_ptr: *mut u8,
    dst_w: u32,
    dst_h: u32,
    dst_stride: u32,
    rx: i32,
    ry: i32,
    rw: i32,
    rh: i32,
    ox: i32,
    oy: i32,
) -> i32 {
    if dst_ptr.is_null() || rw <= 0 || rh <= 0 {
        return -1;
    }
    let dw = dst_w as i32;
    let dh = dst_h as i32;
    let x0 = (ox + rx).max(0);
    let y0 = (oy + ry).max(0);
    let x1 = (ox + rx + rw).min(dw);
    let y1 = (oy + ry + rh).min(dh);
    if x1 <= x0 || y1 <= y0 {
        return 0;
    }
    let d_stride = dst_stride as usize;
    let row_bytes = ((x1 - x0) as usize) * 4;
    for dy in y0..y1 {
        let d_off = (dy as usize) * d_stride + (x0 as usize) * 4;
        unsafe {
            std::ptr::write_bytes(dst_ptr.add(d_off), 0, row_bytes);
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cutora_rust_quantize_partition(
    weights_ptr: *const f32,
    out_ptr: *mut u8,
    n_cells: usize,
    n_pixels: usize,
    hard: i32,
) -> i32 {
    if weights_ptr.is_null() || out_ptr.is_null() || n_cells == 0 || n_pixels == 0 || n_cells > 16 {
        return -1;
    }
    let total_len = n_cells.saturating_mul(n_pixels);
    let weights = unsafe { slice::from_raw_parts(weights_ptr, total_len) };
    let out = unsafe { slice::from_raw_parts_mut(out_ptr, total_len) };

    for p in 0..n_pixels {
        if hard != 0 {
            let mut owner: isize = -1;
            for i in 0..n_cells {
                let w = unsafe { *weights.get_unchecked(i * n_pixels + p) };
                if w >= 0.5 {
                    owner = i as isize;
                }
            }
            for i in 0..n_cells {
                unsafe {
                    *out.get_unchecked_mut(i * n_pixels + p) = if (i as isize) == owner { 255 } else { 0 };
                }
            }
        } else {
            let mut st = [0.0f32; 16];
            let mut rem = 1.0f32;
            for i in (0..n_cells).rev() {
                let w = unsafe { *weights.get_unchecked(i * n_pixels + p) }.clamp(0.0, 1.0);
                let take = rem.min(w);
                st[i] = take;
                rem = (rem - take).max(0.0);
            }
            let mut total = 0.0f32;
            for i in 0..n_cells {
                total += st[i];
            }
            if total <= 1e-6 {
                for i in 0..n_cells {
                    unsafe { *out.get_unchecked_mut(i * n_pixels + p) = 0; }
                }
                continue;
            }
            let inv_total = 255.0f32 / total;
            let mut q = [0i16; 16];
            let mut q_sum: i16 = 0;
            let mut winner: usize = 0;
            let mut max_st: f32 = -1.0;
            for i in 0..n_cells {
                let v = (st[i] * inv_total + 0.5).floor() as i16;
                q[i] = v;
                q_sum += v;
                if st[i] > max_st {
                    max_st = st[i];
                    winner = i;
                }
            }
            let delta = 255i16 - q_sum;
            if delta != 0 {
                q[winner] = (q[winner] + delta).clamp(0, 255);
            }
            for i in 0..n_cells {
                unsafe {
                    *out.get_unchecked_mut(i * n_pixels + p) = q[i] as u8;
                }
            }
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cutora_rust_blit_bgr_solid_rect_copy(
    src_ptr: *const u8,
    src_w: u32,
    src_h: u32,
    src_stride: u32,
    dst_ptr: *mut u8,
    dst_w: u32,
    dst_h: u32,
    dst_stride: u32,
    rx: i32,
    ry: i32,
    rw: i32,
    rh: i32,
    ox: i32,
    oy: i32,
) -> i32 {
    if src_ptr.is_null() || dst_ptr.is_null() || rw <= 0 || rh <= 0 {
        return -1;
    }
    let sw = src_w as i32;
    let sh = src_h as i32;
    let dw = dst_w as i32;
    let dh = dst_h as i32;

    let mut x0 = rx.max(0);
    let mut y0 = ry.max(0);
    let mut x1 = (rx + rw).min(sw);
    let mut y1 = (ry + rh).min(sh);

    if ox + x0 < 0 {
        x0 = -ox;
    }
    if oy + y0 < 0 {
        y0 = -oy;
    }
    if ox + x1 > dw {
        x1 = dw - ox;
    }
    if oy + y1 > dh {
        y1 = dh - oy;
    }
    if x1 <= x0 || y1 <= y0 {
        return 0;
    }

    let s_stride = src_stride as usize;
    let d_stride = dst_stride as usize;
    let span_w = (x1 - x0) as usize;

    for sy in y0..y1 {
        let dy = (oy + sy) as usize;
        let s_off = (sy as usize) * s_stride + (x0 as usize) * 3;
        let d_off = dy * d_stride + ((ox + x0) as usize) * 4;
        unsafe {
            let s_row = src_ptr.add(s_off);
            let d_row = dst_ptr.add(d_off);
            let mut x = 0usize;
            while x + 4 <= span_w {
                let sp = s_row.add(x * 3);
                let dp = d_row.add(x * 4) as *mut u32;
                let w0 = std::ptr::read_unaligned(sp as *const u64);
                let w1 = std::ptr::read_unaligned(sp.add(6) as *const u64);
                let p0 = ((w0 as u32) & 0x00FF_FFFF) | 0xFF00_0000;
                let p1 = (((w0 >> 24) as u32) & 0x00FF_FFFF) | 0xFF00_0000;
                let p2 = ((w1 as u32) & 0x00FF_FFFF) | 0xFF00_0000;
                let p3 = (((w1 >> 24) as u32) & 0x00FF_FFFF) | 0xFF00_0000;
                std::ptr::write_unaligned(dp, p0);
                std::ptr::write_unaligned(dp.add(1), p1);
                std::ptr::write_unaligned(dp.add(2), p2);
                std::ptr::write_unaligned(dp.add(3), p3);
                x += 4;
            }
            while x < span_w {
                let sp = s_row.add(x * 3);
                let dp = d_row.add(x * 4);
                *dp = *sp;
                *dp.add(1) = *sp.add(1);
                *dp.add(2) = *sp.add(2);
                *dp.add(3) = 255;
                x += 1;
            }
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cutora_rust_blit_bgr_masked_band_plus(
    src_ptr: *const u8,
    src_w: u32,
    src_h: u32,
    src_stride: u32,
    mask_ptr: *const u8,
    mask_stride: u32,
    dst_ptr: *mut u8,
    dst_w: u32,
    dst_h: u32,
    dst_stride: u32,
    rx: i32,
    ry: i32,
    rw: i32,
    rh: i32,
    ox: i32,
    oy: i32,
) -> i32 {
    if src_ptr.is_null() || mask_ptr.is_null() || dst_ptr.is_null() || rw <= 0 || rh <= 0 {
        return -1;
    }
    let sw = src_w as i32;
    let sh = src_h as i32;
    let dw = dst_w as i32;
    let dh = dst_h as i32;

    let mut x0 = rx.max(0);
    let mut y0 = ry.max(0);
    let mut x1 = (rx + rw).min(sw);
    let mut y1 = (ry + rh).min(sh);

    if ox + x0 < 0 {
        x0 = -ox;
    }
    if oy + y0 < 0 {
        y0 = -oy;
    }
    if ox + x1 > dw {
        x1 = dw - ox;
    }
    if oy + y1 > dh {
        y1 = dh - oy;
    }
    if x1 <= x0 || y1 <= y0 {
        return 0;
    }

    let s_stride = src_stride as usize;
    let m_stride = mask_stride as usize;
    let d_stride = dst_stride as usize;
    let span_w = (x1 - x0) as usize;

    for sy in y0..y1 {
        let dy = (oy + sy) as usize;
        let s_off = (sy as usize) * s_stride + (x0 as usize) * 3;
        let m_off = (sy as usize) * m_stride + (x0 as usize) * 4;
        let d_off = dy * d_stride + ((ox + x0) as usize) * 4;

        unsafe {
            let s_row = slice::from_raw_parts(src_ptr.add(s_off), span_w * 3);
            let m_row = slice::from_raw_parts(mask_ptr.add(m_off), span_w * 4);
            let d_row = slice::from_raw_parts_mut(dst_ptr.add(d_off), span_w * 4);

            for x in 0..span_w {
                let si = x * 3;
                let di = x * 4;
                let ma = *m_row.get_unchecked(di + 3) as u32;
                if ma == 0 {
                    continue;
                }
                if ma == 255 {
                    *d_row.get_unchecked_mut(di) = (*d_row.get_unchecked(di)).saturating_add(*s_row.get_unchecked(si));
                    *d_row.get_unchecked_mut(di + 1) = (*d_row.get_unchecked(di + 1)).saturating_add(*s_row.get_unchecked(si + 1));
                    *d_row.get_unchecked_mut(di + 2) = (*d_row.get_unchecked(di + 2)).saturating_add(*s_row.get_unchecked(si + 2));
                    *d_row.get_unchecked_mut(di + 3) = 255;
                } else {
                    let sb = (*s_row.get_unchecked(si) as u32 * ma + 127) / 255;
                    let sg = (*s_row.get_unchecked(si + 1) as u32 * ma + 127) / 255;
                    let sr = (*s_row.get_unchecked(si + 2) as u32 * ma + 127) / 255;

                    *d_row.get_unchecked_mut(di) = (*d_row.get_unchecked(di)).saturating_add(sb as u8);
                    *d_row.get_unchecked_mut(di + 1) = (*d_row.get_unchecked(di + 1)).saturating_add(sg as u8);
                    *d_row.get_unchecked_mut(di + 2) = (*d_row.get_unchecked(di + 2)).saturating_add(sr as u8);
                    *d_row.get_unchecked_mut(di + 3) = (*d_row.get_unchecked(di + 3)).saturating_add(ma as u8);
                }
            }
        }
    }
    0
}

#[repr(C)]
#[derive(Copy, Clone)]
pub struct RustBlitOp {
    pub op_type: i32,
    pub src_ptr: *const u8,
    pub src_w: u32,
    pub src_h: u32,
    pub src_stride: u32,
    pub mask_ptr: *const u8,
    pub mask_stride: u32,
    pub rects_ptr: *const i32,
    pub n_rects: usize,
    pub ox: i32,
    pub oy: i32,
}

#[no_mangle]
pub extern "C" fn cutora_rust_exec_blit_ops(
    dst_ptr: *mut u8,
    dst_w: u32,
    dst_h: u32,
    dst_stride: u32,
    ops_ptr: *const RustBlitOp,
    n_ops: usize,
) -> i32 {
    if dst_ptr.is_null() || ops_ptr.is_null() || n_ops == 0 || n_ops > 256 {
        return -1;
    }
    let ops = unsafe { slice::from_raw_parts(ops_ptr, n_ops) };
    for op in ops {
        if op.rects_ptr.is_null() || op.n_rects == 0 {
            continue;
        }
        let r_slice = unsafe { slice::from_raw_parts(op.rects_ptr, op.n_rects * 4) };
        match op.op_type {
            0 => {
                for i in 0..op.n_rects {
                    let b = i * 4;
                    cutora_rust_zero_rect(
                        dst_ptr, dst_w, dst_h, dst_stride,
                        r_slice[b], r_slice[b + 1], r_slice[b + 2], r_slice[b + 3],
                        op.ox, op.oy,
                    );
                }
            }
            1 => {
                for i in 0..op.n_rects {
                    let b = i * 4;
                    cutora_rust_blit_solid_rect_copy(
                        op.src_ptr, op.src_w, op.src_h, op.src_stride,
                        dst_ptr, dst_w, dst_h, dst_stride,
                        r_slice[b], r_slice[b + 1], r_slice[b + 2], r_slice[b + 3],
                        op.ox, op.oy,
                    );
                }
            }
            2 => {
                for i in 0..op.n_rects {
                    let b = i * 4;
                    cutora_rust_blit_masked_band_plus(
                        op.src_ptr, op.src_w, op.src_h, op.src_stride,
                        op.mask_ptr, op.mask_stride,
                        dst_ptr, dst_w, dst_h, dst_stride,
                        r_slice[b], r_slice[b + 1], r_slice[b + 2], r_slice[b + 3],
                        op.ox, op.oy,
                    );
                }
            }
            3 => {
                for i in 0..op.n_rects {
                    let b = i * 4;
                    cutora_rust_blit_bgr_solid_rect_copy(
                        op.src_ptr, op.src_w, op.src_h, op.src_stride,
                        dst_ptr, dst_w, dst_h, dst_stride,
                        r_slice[b], r_slice[b + 1], r_slice[b + 2], r_slice[b + 3],
                        op.ox, op.oy,
                    );
                }
            }
            4 => {
                for i in 0..op.n_rects {
                    let b = i * 4;
                    cutora_rust_blit_bgr_masked_band_plus(
                        op.src_ptr, op.src_w, op.src_h, op.src_stride,
                        op.mask_ptr, op.mask_stride,
                        dst_ptr, dst_w, dst_h, dst_stride,
                        r_slice[b], r_slice[b + 1], r_slice[b + 2], r_slice[b + 3],
                        op.ox, op.oy,
                    );
                }
            }
            _ => {}
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cutora_rust_premask_cell_qimg(
    src_ptr: *const u8,
    mask_ptr: *const u8,
    dst_ptr: *mut u8,
    width: u32,
    height: u32,
    src_stride: u32,
    mask_stride: u32,
    dst_stride: u32,
) -> i32 {
    if src_ptr.is_null() || dst_ptr.is_null() || width == 0 || height == 0 {
        return -1;
    }
    let w = width as usize;
    let h = height as usize;
    let s_st = src_stride as usize;
    let m_st = mask_stride as usize;
    let d_st = dst_stride as usize;

    if mask_ptr.is_null() {
        for y in 0..h {
            unsafe {
                std::ptr::copy_nonoverlapping(src_ptr.add(y * s_st), dst_ptr.add(y * d_st), w * 4);
            }
        }
        return 0;
    }

    for y in 0..h {
        unsafe {
            let s_row = slice::from_raw_parts(src_ptr.add(y * s_st), w * 4);
            let m_row = slice::from_raw_parts(mask_ptr.add(y * m_st), w * 4);
            let d_row = slice::from_raw_parts_mut(dst_ptr.add(y * d_st), w * 4);
            for x in 0..w {
                let i = x * 4;
                let ma = *m_row.get_unchecked(i + 3) as u32;
                if ma == 0 {
                    *(d_row.as_mut_ptr().add(i) as *mut u32) = 0;
                } else if ma == 255 {
                    *(d_row.as_mut_ptr().add(i) as *mut u32) = *(s_row.as_ptr().add(i) as *const u32);
                } else {
                    let sb = (*s_row.get_unchecked(i) as u32 * ma + 127) / 255;
                    let sg = (*s_row.get_unchecked(i + 1) as u32 * ma + 127) / 255;
                    let sr = (*s_row.get_unchecked(i + 2) as u32 * ma + 127) / 255;
                    let sa = (*s_row.get_unchecked(i + 3) as u32 * ma + 127) / 255;
                    let px = sb | (sg << 8) | (sr << 16) | (sa << 24);
                    *(d_row.as_mut_ptr().add(i) as *mut u32) = px;
                }
            }
        }
    }
    0
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn bgra_to_yuv420p_avx2(
    src_ptr: *const u8,
    w: usize,
    h: usize,
    s_stride: usize,
    y_plane: *mut u8,
    u_plane: *mut u8,
    v_plane: *mut u8,
) {
    use std::arch::x86_64::*;
    let uv_w = w / 2;
    let uv_h = h / 2;

    let c_y_main = _mm256_setr_epi8(
        25, 65, 66, 0, 25, 65, 66, 0, 25, 65, 66, 0, 25, 65, 66, 0,
        25, 65, 66, 0, 25, 65, 66, 0, 25, 65, 66, 0, 25, 65, 66, 0,
    );
    let c_y_g2 = _mm256_setr_epi8(
        0, 64, 0, 0, 0, 64, 0, 0, 0, 64, 0, 0, 0, 64, 0, 0,
        0, 64, 0, 0, 0, 64, 0, 0, 0, 64, 0, 0, 0, 64, 0, 0,
    );
    let c_u = _mm256_setr_epi8(
        112, -74, -38, 0, 112, -74, -38, 0, 112, -74, -38, 0, 112, -74, -38, 0,
        112, -74, -38, 0, 112, -74, -38, 0, 112, -74, -38, 0, 112, -74, -38, 0,
    );
    let c_v = _mm256_setr_epi8(
        -18, -94, 112, 0, -18, -94, 112, 0, -18, -94, 112, 0, -18, -94, 112, 0,
        -18, -94, 112, 0, -18, -94, 112, 0, -18, -94, 112, 0, -18, -94, 112, 0,
    );
    let ones_16 = _mm256_set1_epi16(1);
    let y_bias = _mm256_set1_epi32(4224); // 128 + (16 << 8)
    let uv_bias = _mm256_set1_epi32(65792); // 256 + (128 << 9)
    let perm_mask = _mm256_setr_epi32(0, 4, 1, 5, 2, 6, 3, 7);

    let simd_blocks = uv_w & !3; // 4 uv blocks = 8 pixels per iteration

    for by in 0..uv_h {
        let y0 = by * 2;
        let y1 = y0 + 1;
        let s_row0 = src_ptr.add(y0 * s_stride);
        let s_row1 = src_ptr.add(y1 * s_stride);
        let y_row0 = y_plane.add(y0 * w);
        let y_row1 = y_plane.add(y1 * w);
        let u_row = u_plane.add(by * uv_w);
        let v_row = v_plane.add(by * uv_w);

        let mut bx = 0usize;
        while bx < simd_blocks {
            let x0 = bx * 2;
            let v0 = _mm256_loadu_si256(s_row0.add(x0 * 4) as *const __m256i);
            let v1 = _mm256_loadu_si256(s_row1.add(x0 * 4) as *const __m256i);

            let y0_sum32 = _mm256_add_epi32(
                _mm256_madd_epi16(_mm256_maddubs_epi16(v0, c_y_main), ones_16),
                _mm256_madd_epi16(_mm256_maddubs_epi16(v0, c_y_g2), ones_16),
            );
            let y1_sum32 = _mm256_add_epi32(
                _mm256_madd_epi16(_mm256_maddubs_epi16(v1, c_y_main), ones_16),
                _mm256_madd_epi16(_mm256_maddubs_epi16(v1, c_y_g2), ones_16),
            );

            let y0_32 = _mm256_srli_epi32(_mm256_add_epi32(y0_sum32, y_bias), 8);
            let y1_32 = _mm256_srli_epi32(_mm256_add_epi32(y1_sum32, y_bias), 8);

            let y_pack16 = _mm256_packs_epi32(y0_32, y1_32);
            let y_pack8 = _mm256_packus_epi16(y_pack16, y_pack16);
            let y_perm = _mm256_permutevar8x32_epi32(y_pack8, perm_mask);
            let y_lo = _mm256_castsi256_si128(y_perm);
            std::ptr::write_unaligned(y_row0.add(x0) as *mut i64, _mm_extract_epi64(y_lo, 0));
            std::ptr::write_unaligned(y_row1.add(x0) as *mut i64, _mm_extract_epi64(y_lo, 1));

            let v_avg = _mm256_avg_epu8(v0, v1);
            let u_32 = _mm256_madd_epi16(_mm256_maddubs_epi16(v_avg, c_u), ones_16);
            let v_32 = _mm256_madd_epi16(_mm256_maddubs_epi16(v_avg, c_v), ones_16);

            let uv_sum = _mm256_hadd_epi32(u_32, v_32);
            let uv_shifted = _mm256_srai_epi32(_mm256_add_epi32(uv_sum, uv_bias), 9);
            let uv_16 = _mm256_packs_epi32(uv_shifted, uv_shifted);
            let uv_8 = _mm256_packus_epi16(uv_16, uv_16);
            // Lane 0 low 32 bits: [u0, u1, v0, v1]; Lane 1 low 32 bits: [u2, u3, v2, v3]
            let lane0 = _mm256_extract_epi32(uv_8, 0) as u32;
            let lane1 = _mm256_extract_epi32(uv_8, 4) as u32;
            let u4 = (lane0 & 0xFFFF) | ((lane1 & 0xFFFF) << 16);
            let v4 = (lane0 >> 16) | (lane1 & 0xFFFF_0000);
            std::ptr::write_unaligned(u_row.add(bx) as *mut u32, u4);
            std::ptr::write_unaligned(v_row.add(bx) as *mut u32, v4);

            bx += 4;
        }

        while bx < uv_w {
            let x0 = bx * 2;
            let w0 = std::ptr::read_unaligned(s_row0.add(x0 * 4) as *const u64);
            let w1 = std::ptr::read_unaligned(s_row1.add(x0 * 4) as *const u64);

            let b00 = (w0 & 0xFF) as i32;
            let g00 = ((w0 >> 8) & 0xFF) as i32;
            let r00 = ((w0 >> 16) & 0xFF) as i32;

            let b01 = ((w0 >> 32) & 0xFF) as i32;
            let g01 = ((w0 >> 40) & 0xFF) as i32;
            let r01 = ((w0 >> 48) & 0xFF) as i32;

            let b10 = (w1 & 0xFF) as i32;
            let g10 = ((w1 >> 8) & 0xFF) as i32;
            let r10 = ((w1 >> 16) & 0xFF) as i32;

            let b11 = ((w1 >> 32) & 0xFF) as i32;
            let g11 = ((w1 >> 40) & 0xFF) as i32;
            let r11 = ((w1 >> 48) & 0xFF) as i32;

            let y00 = (((66 * r00 + 129 * g00 + 25 * b00 + 128) >> 8) + 16) as u16;
            let y01 = (((66 * r01 + 129 * g01 + 25 * b01 + 128) >> 8) + 16) as u16;
            let y10 = (((66 * r10 + 129 * g10 + 25 * b10 + 128) >> 8) + 16) as u16;
            let y11 = (((66 * r11 + 129 * g11 + 25 * b11 + 128) >> 8) + 16) as u16;

            std::ptr::write_unaligned(y_row0.add(x0) as *mut u16, y00 | (y01 << 8));
            std::ptr::write_unaligned(y_row1.add(x0) as *mut u16, y10 | (y11 << 8));

            let r_sum = r00 + r01 + r10 + r11;
            let g_sum = g00 + g01 + g10 + g11;
            let b_sum = b00 + b01 + b10 + b11;

            *u_row.add(bx) = (((-38 * r_sum - 74 * g_sum + 112 * b_sum + 512) >> 10) + 128) as u8;
            *v_row.add(bx) = (((112 * r_sum - 94 * g_sum - 18 * b_sum + 512) >> 10) + 128) as u8;
            bx += 1;
        }
    }
}

#[no_mangle]
pub extern "C" fn cutora_rust_bgra_to_yuv420p(
    src_ptr: *const u8,
    width: u32,
    height: u32,
    src_stride: u32,
    dst_ptr: *mut u8,
) -> i32 {
    if src_ptr.is_null() || dst_ptr.is_null() || width == 0 || height == 0 || (width & 1) != 0 || (height & 1) != 0 {
        return -1;
    }
    let w = width as usize;
    let h = height as usize;
    let s_stride = src_stride as usize;
    if s_stride < w * 4 {
        return -2;
    }
    let y_size = w * h;
    let uv_w = w / 2;
    let uv_h = h / 2;
    let u_size = uv_w * uv_h;

    let y_plane = dst_ptr;
    let u_plane = unsafe { dst_ptr.add(y_size) };
    let v_plane = unsafe { dst_ptr.add(y_size + u_size) };

    #[cfg(target_arch = "x86_64")]
    {
        if std::is_x86_feature_detected!("avx2") {
            unsafe {
                bgra_to_yuv420p_avx2(src_ptr, w, h, s_stride, y_plane, u_plane, v_plane);
            }
            return 0;
        }
    }

    for by in 0..uv_h {
        let y0 = by * 2;
        let y1 = y0 + 1;
        unsafe {
            let s_row0 = src_ptr.add(y0 * s_stride);
            let s_row1 = src_ptr.add(y1 * s_stride);
            let y_row0 = y_plane.add(y0 * w);
            let y_row1 = y_plane.add(y1 * w);
            let u_row = u_plane.add(by * uv_w);
            let v_row = v_plane.add(by * uv_w);

            for bx in 0..uv_w {
                let x0 = bx * 2;
                let w0 = std::ptr::read_unaligned(s_row0.add(x0 * 4) as *const u64);
                let w1 = std::ptr::read_unaligned(s_row1.add(x0 * 4) as *const u64);

                let b00 = (w0 & 0xFF) as i32;
                let g00 = ((w0 >> 8) & 0xFF) as i32;
                let r00 = ((w0 >> 16) & 0xFF) as i32;

                let b01 = ((w0 >> 32) & 0xFF) as i32;
                let g01 = ((w0 >> 40) & 0xFF) as i32;
                let r01 = ((w0 >> 48) & 0xFF) as i32;

                let b10 = (w1 & 0xFF) as i32;
                let g10 = ((w1 >> 8) & 0xFF) as i32;
                let r10 = ((w1 >> 16) & 0xFF) as i32;

                let b11 = ((w1 >> 32) & 0xFF) as i32;
                let g11 = ((w1 >> 40) & 0xFF) as i32;
                let r11 = ((w1 >> 48) & 0xFF) as i32;

                let y00 = (((66 * r00 + 129 * g00 + 25 * b00 + 128) >> 8) + 16) as u16;
                let y01 = (((66 * r01 + 129 * g01 + 25 * b01 + 128) >> 8) + 16) as u16;
                let y10 = (((66 * r10 + 129 * g10 + 25 * b10 + 128) >> 8) + 16) as u16;
                let y11 = (((66 * r11 + 129 * g11 + 25 * b11 + 128) >> 8) + 16) as u16;

                std::ptr::write_unaligned(y_row0.add(x0) as *mut u16, y00 | (y01 << 8));
                std::ptr::write_unaligned(y_row1.add(x0) as *mut u16, y10 | (y11 << 8));

                let r_sum = r00 + r01 + r10 + r11;
                let g_sum = g00 + g01 + g10 + g11;
                let b_sum = b00 + b01 + b10 + b11;

                *u_row.add(bx) = (((-38 * r_sum - 74 * g_sum + 112 * b_sum + 512) >> 10) + 128) as u8;
                *v_row.add(bx) = (((112 * r_sum - 94 * g_sum - 18 * b_sum + 512) >> 10) + 128) as u8;
            }
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cutora_rust_bgr_resize_crop_premask_qimg(
    src_ptr: *const u8,
    src_w: u32,
    src_h: u32,
    src_stride: u32,
    mask_ptr: *const u8,
    mask_stride: u32,
    dst_ptr: *mut u8,
    dst_w: u32,
    dst_h: u32,
    dst_stride: u32,
    full_w: u32,
    full_h: u32,
    off_x: i32,
    off_y: i32,
) -> i32 {
    if src_ptr.is_null() || dst_ptr.is_null() || src_w == 0 || src_h == 0 || dst_w == 0 || dst_h == 0 || full_w == 0 || full_h == 0 {
        return -1;
    }
    let sw = src_w as usize;
    let sh = src_h as usize;
    let dw = dst_w as usize;
    let dh = dst_h as usize;
    let fw = full_w as usize;
    let fh = full_h as usize;
    let s_stride = src_stride as usize;
    let m_stride = mask_stride as usize;
    let d_stride = dst_stride as usize;

    let scale_x = (sw as f32) / (fw as f32);
    let scale_y = (sh as f32) / (fh as f32);

    let mut x_idx0 = Vec::with_capacity(dw);
    let mut x_idx1 = Vec::with_capacity(dw);
    let mut x_w1 = Vec::with_capacity(dw);
    for dx in 0..dw {
        let gx = (dx as i32 + off_x).clamp(0, (fw as i32) - 1) as f32;
        let fx = (gx + 0.5) * scale_x - 0.5;
        let x0 = if fx < 0.0 { 0usize } else { (fx as usize).min(sw - 1) };
        let x1 = (x0 + 1).min(sw - 1);
        let frac = if fx < 0.0 { 0.0 } else { (fx - (x0 as f32)).clamp(0.0, 1.0) };
        let w1 = (frac * 256.0 + 0.5) as u32;
        x_idx0.push(x0 * 3);
        x_idx1.push(x1 * 3);
        x_w1.push(w1);
    }

    let has_mask = !mask_ptr.is_null() && m_stride >= dw * 4;

    for dy in 0..dh {
        let gy = (dy as i32 + off_y).clamp(0, (fh as i32) - 1) as f32;
        let fy = (gy + 0.5) * scale_y - 0.5;
        let y0 = if fy < 0.0 { 0usize } else { (fy as usize).min(sh - 1) };
        let y1 = (y0 + 1).min(sh - 1);
        let frac_y = if fy < 0.0 { 0.0 } else { (fy - (y0 as f32)).clamp(0.0, 1.0) };
        let wy1 = (frac_y * 256.0 + 0.5) as u32;
        let wy0 = 256 - wy1;

        unsafe {
            let s_row0 = src_ptr.add(y0 * s_stride);
            let s_row1 = src_ptr.add(y1 * s_stride);
            let d_row = dst_ptr.add(dy * d_stride);

            if has_mask {
                let m_row = mask_ptr.add(dy * m_stride);
                for dx in 0..dw {
                    let ma = *m_row.add(dx * 4 + 3) as u32;
                    if ma == 0 {
                        (d_row.add(dx * 4) as *mut u32).write_unaligned(0);
                        continue;
                    }
                    let i0 = *x_idx0.get_unchecked(dx);
                    let i1 = *x_idx1.get_unchecked(dx);
                    let wx1 = *x_w1.get_unchecked(dx);
                    let wx0 = 256 - wx1;

                    let sp00 = s_row0.add(i0);
                    let sp01 = s_row0.add(i1);
                    let sp10 = s_row1.add(i0);
                    let sp11 = s_row1.add(i1);

                    let b0 = (*sp00 as u32 * wx0 + *sp01 as u32 * wx1) * wy0
                        + (*sp10 as u32 * wx0 + *sp11 as u32 * wx1) * wy1;
                    let g0 = (*sp00.add(1) as u32 * wx0 + *sp01.add(1) as u32 * wx1) * wy0
                        + (*sp10.add(1) as u32 * wx0 + *sp11.add(1) as u32 * wx1) * wy1;
                    let r0 = (*sp00.add(2) as u32 * wx0 + *sp01.add(2) as u32 * wx1) * wy0
                        + (*sp10.add(2) as u32 * wx0 + *sp11.add(2) as u32 * wx1) * wy1;

                    let mut b = (b0 + 32768) >> 16;
                    let mut g = (g0 + 32768) >> 16;
                    let mut r = (r0 + 32768) >> 16;
                    if ma < 255 {
                        b = (b * ma + 127) / 255;
                        g = (g * ma + 127) / 255;
                        r = (r * ma + 127) / 255;
                    }
                    let pixel = b | (g << 8) | (r << 16) | (ma << 24);
                    (d_row.add(dx * 4) as *mut u32).write_unaligned(pixel);
                }
            } else {
                for dx in 0..dw {
                    let i0 = *x_idx0.get_unchecked(dx);
                    let i1 = *x_idx1.get_unchecked(dx);
                    let wx1 = *x_w1.get_unchecked(dx);
                    let wx0 = 256 - wx1;

                    let sp00 = s_row0.add(i0);
                    let sp01 = s_row0.add(i1);
                    let sp10 = s_row1.add(i0);
                    let sp11 = s_row1.add(i1);

                    let b0 = (*sp00 as u32 * wx0 + *sp01 as u32 * wx1) * wy0
                        + (*sp10 as u32 * wx0 + *sp11 as u32 * wx1) * wy1;
                    let g0 = (*sp00.add(1) as u32 * wx0 + *sp01.add(1) as u32 * wx1) * wy0
                        + (*sp10.add(1) as u32 * wx0 + *sp11.add(1) as u32 * wx1) * wy1;
                    let r0 = (*sp00.add(2) as u32 * wx0 + *sp01.add(2) as u32 * wx1) * wy0
                        + (*sp10.add(2) as u32 * wx0 + *sp11.add(2) as u32 * wx1) * wy1;

                    let b = (b0 + 32768) >> 16;
                    let g = (g0 + 32768) >> 16;
                    let r = (r0 + 32768) >> 16;
                    let pixel = b | (g << 8) | (r << 16) | 0xFF00_0000;
                    (d_row.add(dx * 4) as *mut u32).write_unaligned(pixel);
                }
            }
        }
    }
    0
}



