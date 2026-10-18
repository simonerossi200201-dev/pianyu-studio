# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""空间立体声自检 —— 在 Windows 上用真正的 ffmpeg.exe 跑一遍, 量给你看。

用法(在 core 目录下):
    runtime\\venv\\Scripts\\python.exe -m cutora.audio.spatial_selftest

它会:
  1. 合成一段【单声道】类语音信号(这是最容易翻车的情况);
  2. 走一遍和导出完全一样的滤镜图;
  3. 量四个数并判定通过与否。

四个数分别是什么意思:
  · 净增益        开效果前后整体响度差。应该在 ±1 dB 内 —— 老版本这里掉 3~5 dB。
  · side/mid      侧信号占比。0 = 纯单声道(老版本在单声道素材上恒为 0)。
  · 空档混响电平  词与词之间混响尾巴的电平, 相对语音本体。−8~−13 dB 之间最合适:
                  太低听不出空间, 太高开始糊字。
  · 空档 side/mid 空档里的宽度。应该【明显大于】说话时的宽度 —— 这说明侧链压缩
                  在干活: 讲话时混响让位, 字缝里混响涨起来。
"""
import math
import os
import struct
import subprocess
import sys
import wave
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FFMPEG = os.path.join(HERE, "ffmpeg.exe")
SR = 48000
WORDS = [(0.2, 0.75), (1.0, 1.7), (2.1, 2.5), (3.0, 3.9), (4.4, 4.9)]


def make_mono(path):
    """合成 6 秒单声道类语音。只用标准库, 不依赖 numpy。"""
    n = SR * 6
    buf = [0.0] * n
    seed = 12345
    for on, off in WORDS:
        for i in range(int(on * SR), int(off * SR)):
            tt = (i - on * SR) / SR
            f0 = 115.0 + 20.0 * math.sin(2 * math.pi * 3 * tt)
            v = 0.0
            for h in range(1, 11):
                v += math.sin(2 * math.pi * f0 * h * tt) / h
            seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
            v += 0.25 * ((seed / 0x3FFFFFFF) - 1.0) * abs(math.sin(2 * math.pi * 7 * tt))
            env = min(1.0, tt / 0.02) * min(1.0, (off - on - tt) / 0.05)
            buf[i] = v * env
    peak = max(abs(x) for x in buf) or 1.0
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(32767 * 0.35 * x / peak)) for x in buf))


def read_wav(path):
    with wave.open(path, "rb") as w:
        ch, n = w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    d = struct.unpack("<%dh" % (len(raw) // 2), raw)
    return [[d[i * ch + c] / 32768.0 for c in range(ch)] for i in range(len(d) // ch)]


def rms(xs):
    return math.sqrt(sum(x * x for x in xs) / max(1, len(xs)) + 1e-20)


def db(x, ref):
    return 20 * math.log10(max(1e-9, x) / max(1e-9, ref))


def stats(fr, a, b):
    seg = fr[int(a * SR):int(b * SR)]
    if not seg:
        return 0.0, 0.0
    mid = [(s[0] + s[-1]) / 2 for s in seg]
    side = [(s[0] - s[-1]) / 2 for s in seg]
    return rms([x for s in seg for x in s]), rms(side) / max(1e-9, rms(mid))


def main():
    if not os.path.isfile(FFMPEG):
        print("找不到 ffmpeg.exe:", FFMPEG); return 1
    from cutora.audio import spatial as S
    S.set_ffmpeg_path(FFMPEG)
    print("ffmpeg 的 afir 归一化选项 =", repr(S._afir_norm_opt()),
          "(None = 探测失败, 会退回线性链路)")

    src = os.path.join(HERE, "user_data", "_spatial_selftest_in.wav")
    out = os.path.join(HERE, "user_data", "_spatial_selftest_out.wav")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    make_mono(src)

    ok_all = True
    # 最后多跑一遍 stage + 话筒质感, 确认质感链路也能过
    cases = [(p, 0.0) for p in S.PRESETS] + [("stage", 0.5)]
    for preset, mic in cases:
        chain = S.build_hall_reverb_filter(preset, uid=0, src_path=src, mic=mic)
        cmd = [FFMPEG, "-y", "-v", "error", "-i", src]
        if S.is_graph(chain):
            cmd += ["-filter_complex", "[0:a]%s[aout]" % chain, "-map", "[aout]"]
        else:
            cmd += ["-af", chain]
        cmd += ["-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", out]
        r = _no_window_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 0:
            print("\n[%s] ffmpeg 失败:\n%s" % (preset, r.stderr.decode("utf-8", "ignore")[-500:]))
            ok_all = False
            continue

        ref = read_wav(src); got = read_wav(out)
        base = rms([s[0] for s in ref])
        gain = db(rms([x for s in got for x in s]), base)
        _, sm_speak = stats(got, 1.0, 1.7)
        gl, sm_gap = [], []
        for i, (_, off) in enumerate(WORDS):
            g1 = min(off + 0.45, WORDS[i + 1][0] if i + 1 < len(WORDS) else 6.0)
            if g1 - off < 0.1:
                continue
            v, s = stats(got, off, g1)
            gl.append(v); sm_gap.append(s)
        gap_db = db(sum(gl) / len(gl), base)
        conv = ("卷积" if S.is_graph(chain) else "线性兜底") + (" +话筒质感" if mic else "")
        # 空档混响的合格区间放得比较宽, 因为几个预设本来就有干有湿(speech 最干、
        # cathedral 最湿)。这一项只用来抓两种真故障: 完全没混响, 和湿到离谱。
        checks = [abs(gain) < 1.5, sm_speak > 0.01, -25.0 < gap_db < -4.0,
                  sum(sm_gap) / len(sm_gap) > sm_speak * 2]
        print("\n[%s] %s  %s" % (preset, conv, "通过" if all(checks) else "!! 有指标越界"))
        print("   净增益        %+6.2f dB      (要求 |x| < 1.5)" % gain)
        print("   说话时 side/mid %6.3f       (要求 > 0, 否则宽度没生效)" % sm_speak)
        print("   空档混响电平  %+6.2f dB      (要求 −25 ~ −4; 越接近 −9 空间感越明显)" % gap_db)
        print("   空档 side/mid  %6.3f       (要求 > 说话时的 2 倍 = ducking 生效)"
              % (sum(sm_gap) / len(sm_gap)))
        ok_all = ok_all and all(checks)

    for p in (src, out):
        try:
            os.remove(p)
        except OSError:
            pass
    print("\n总判定:", "全部通过" if ok_all else "有项目没过, 把上面的输出发我")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
