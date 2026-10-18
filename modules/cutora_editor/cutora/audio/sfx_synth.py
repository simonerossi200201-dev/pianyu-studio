# SPDX-License-Identifier: GPL-3.0-or-later
"""程序化生成音效库 WAV（48kHz 立体声 16bit）。

大型现场：鼓掌、Amen 应和、大厅同期声。
自然环境：鸟叫、海浪、河流、雨、风。

Amen 在 Windows 上优先用系统 SAPI 合成人声再叠成群众；失败则回退到共振峰。
可单独运行：  python sfx_synth.py
"""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
import wave

import numpy as np
from scipy.signal import lfilter, butter, fftconvolve
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

SR = 48000

def _rng(seed):
    return np.random.default_rng(seed)

def fade(n, fade_in=0.02, fade_out=0.08, sr=SR):
    env = np.ones(n, dtype=np.float64)
    ai = min(n // 3, max(1, int(fade_in * sr)))
    ao = min(n // 3, max(1, int(fade_out * sr)))
    env[:ai] *= np.linspace(0.0, 1.0, ai, endpoint=True)
    env[-ao:] *= np.linspace(1.0, 0.0, ao, endpoint=True)
    return env

def peak_normalize(stereo, peak=0.89):
    m = np.max(np.abs(stereo))
    if m < 1e-9:
        return stereo
    return stereo * (peak / m)

def to_stereo(x, pan=0.0):
    """x: (n,) pan -1..1 → (n, 2)"""
    g_l = math.sqrt((1.0 - pan) * 0.5)
    g_r = math.sqrt((1.0 + pan) * 0.5)
    return np.column_stack([x * g_l, x * g_r])

def one_pole_lp(x, cutoff, sr=SR):
    cutoff = min(max(1.0, float(cutoff)), sr * 0.45)
    b, a = butter(1, cutoff / (sr * 0.5), btype="low")
    return lfilter(b, a, x)

def one_pole_hp(x, cutoff, sr=SR):
    cutoff = min(max(1.0, float(cutoff)), sr * 0.45)
    b, a = butter(1, cutoff / (sr * 0.5), btype="high")
    return lfilter(b, a, x)

def biquad_bp(x, f0, q, sr=SR):
    """RBJ bandpass via scipy lfilter."""
    f0 = min(max(20.0, float(f0)), sr * 0.45)
    w0 = 2.0 * math.pi * f0 / sr
    alpha = math.sin(w0) / (2.0 * max(0.1, q))
    b0 = alpha
    b1 = 0.0
    b2 = -alpha
    a0 = 1.0 + alpha
    a1 = -2.0 * math.cos(w0)
    a2 = 1.0 - alpha
    b = [b0 / a0, b1 / a0, b2 / a0]
    a = [1.0, a1 / a0, a2 / a0]
    return lfilter(b, a, x)

def pink_like(n, rng):
    """Voss-McCartney 近似粉红噪声。"""
    octaves = 6
    white = rng.standard_normal((octaves, n))
    out = np.zeros(n)
    step = 1
    for o in range(octaves):
        idx = np.arange(n) // step
        vals = rng.standard_normal(int(idx[-1]) + 2)
        out += vals[idx]
        step *= 2
    out += white[0]
    out /= np.std(out) + 1e-9
    return out

def reverb_tail(stereo, rt60=1.8, sr=SR):
    """廉价立体声尾响：指数衰减噪声 IR + FFT 卷积。"""
    n = stereo.shape[0]
    ir_n = min(int(rt60 * sr * 0.35), 24000)
    t = np.arange(ir_n) / sr
    env = np.exp(-3.0 * t / max(0.2, rt60))
    rng = _rng(901 + ir_n)
    ir_l = rng.standard_normal(ir_n) * env
    ir_r = rng.standard_normal(ir_n) * env
    ir_l[0] = 1.0
    ir_r[0] = 1.0
    wet_l = fftconvolve(stereo[:, 0], ir_l, mode="full")[:n]
    wet_r = fftconvolve(stereo[:, 1], ir_r, mode="full")[:n]
    dry, wet = 0.78, 0.22
    return np.column_stack([stereo[:, 0] * dry + wet_l * wet,
                            stereo[:, 1] * dry + wet_r * wet])

def write_wav(path, stereo, sr=SR):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stereo = peak_normalize(np.asarray(stereo, dtype=np.float64))
    pcm = np.clip(stereo * 32767.0, -32767, 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    print("  wrote", os.path.basename(path), f"{stereo.shape[0]/sr:.1f}s")

# ── 鼓掌 ──────────────────────────────────────────────────────────────────
def synth_applause(duration, intensity=1.0, n_people=140, seed=1, whoops=True):
    rng = _rng(seed)
    n = int(SR * duration)
    out = np.zeros((n, 2), dtype=np.float64)
    env = fade(n, 0.18, 0.9)
    env *= np.clip(0.55 + 0.45 * np.sin(np.linspace(0, math.pi, n)), 0.2, 1.0)

    for _ in range(n_people):
        pan = float(rng.uniform(-0.9, 0.9))
        rate = float(rng.uniform(1.7, 4.4))
        t = float(rng.uniform(0.0, 1.0 / rate))
        g = float(rng.uniform(0.25, 1.0)) * intensity
        while t < duration:
            clen = int(rng.uniform(0.005, 0.016) * SR)
            start = int(t * SR)
            if start + clen >= n:
                break
            noise = rng.standard_normal(clen)
            decay = np.exp(-np.linspace(0, rng.uniform(6, 12), clen))
            clap = biquad_bp(noise, rng.uniform(1400, 3200), rng.uniform(0.6, 1.4)) * decay
            sl = clap * g * math.sqrt((1 - pan) * 0.5)
            sr_ = clap * g * math.sqrt((1 + pan) * 0.5)
            out[start:start + clen, 0] += sl
            out[start:start + clen, 1] += sr_
            t += 1.0 / rate + float(rng.uniform(-0.04, 0.07))

    if whoops:
        for _ in range(max(2, int(duration * 0.6))):
            t0 = float(rng.uniform(0.3, duration - 0.4))
            d = float(rng.uniform(0.18, 0.45))
            f0 = float(rng.uniform(280, 520))
            f1 = f0 * float(rng.uniform(1.15, 1.6))
            nn = int(d * SR)
            s0 = int(t0 * SR)
            if s0 + nn >= n:
                continue
            tt = np.arange(nn) / SR
            freq = np.linspace(f0, f1, nn)
            phase = np.cumsum(2 * math.pi * freq / SR)
            whoop = np.sin(phase) * np.exp(-tt * 4.5) * float(rng.uniform(0.04, 0.10))
            pan = float(rng.uniform(-0.6, 0.6))
            out[s0:s0 + nn] += to_stereo(whoop, pan)

    out *= env[:, None]
    return reverb_tail(out, rt60=1.6)

# ── 大厅同期声 ────────────────────────────────────────────────────────────
def synth_venue(duration, seed=7, with_murmur=True):
    rng = _rng(seed)
    n = int(SR * duration)
    t = np.arange(n) / SR
    brown = np.cumsum(rng.standard_normal(n))
    brown = brown / (np.max(np.abs(brown)) + 1e-9)
    hvac = one_pole_lp(brown, 90) * 0.18
    air = one_pole_lp(pink_like(n, rng), 1800) * 0.12
    lfo = 0.85 + 0.15 * np.sin(2 * math.pi * 0.07 * t)
    base = (hvac + air) * lfo
    # 左右略不同
    delay = int(0.012 * SR)
    left = base.copy()
    right = np.concatenate([np.zeros(delay), base[:-delay]])
    right += one_pole_lp(rng.standard_normal(n), 400) * 0.03
    out = np.column_stack([left, right])

    if with_murmur:
        for i in range(18):
            f1 = float(rng.uniform(250, 700))
            f2 = float(rng.uniform(800, 1800))
            f0 = float(rng.uniform(90, 180))
            pan = float(rng.uniform(-0.8, 0.8))
            amp = float(rng.uniform(0.012, 0.035))
            vib = 1.0 + 0.03 * np.sin(2 * math.pi * rng.uniform(2.5, 4.5) * t)
            src = (np.sin(2 * math.pi * f0 * t * vib)
                   + 0.4 * np.sin(2 * math.pi * f1 * t)
                   + 0.18 * np.sin(2 * math.pi * f2 * t))
            src *= (0.6 + 0.4 * np.sin(2 * math.pi * rng.uniform(0.15, 0.5) * t + i))
            src = one_pole_lp(src, 2200) * amp
            out += to_stereo(src, pan)

    # 偶尔翻页/轻咳
    for _ in range(4):
        t0 = float(rng.uniform(1.0, duration - 0.5))
        clen = int(rng.uniform(0.04, 0.12) * SR)
        s0 = int(t0 * SR)
        if s0 + clen >= n:
            continue
        click = rng.standard_normal(clen) * np.exp(-np.linspace(0, 10, clen)) * 0.08
        click = one_pole_hp(click, 800)
        pan = float(rng.uniform(-0.7, 0.7))
        out[s0:s0 + clen] += to_stereo(click, pan)

    out *= fade(n, 0.6, 0.8)[:, None]
    return reverb_tail(out, rt60=2.4)

def synth_murmur(duration, seed=11):
    return synth_venue(duration, seed=seed, with_murmur=True)

# ── Amen / 应和 ───────────────────────────────────────────────────────────
def _formant_vowel(n, f0, formants, rng):
    t = np.arange(n) / SR
    # 脉冲串 + 共振峰滤波
    phase = 2 * math.pi * np.cumsum(np.full(n, f0) / SR)
    # 轻微抖动
    phase += np.cumsum(rng.standard_normal(n) * 0.02)
    glot = np.maximum(0.0, np.sin(phase)) ** 2
    y = np.zeros(n)
    for f, q, g in formants:
        y += biquad_bp(glot, f, q) * g
    y += rng.standard_normal(n) * 0.02
    return y

def _synth_amen_one(duration, f0, rng, strength=1.0):
    """ah (60%) + men (40%) 的简化音节。"""
    n = int(SR * duration)
    n_ah = int(n * 0.48)
    n_m = n - n_ah
    ah = _formant_vowel(n_ah, f0, [(700, 8, 1.0), (1200, 10, 0.7), (2500, 12, 0.25)], rng)
    men = _formant_vowel(n_m, f0 * 0.96, [(450, 7, 1.0), (1000, 9, 0.45), (2300, 11, 0.2)], rng)
    # m 鼻音
    men = one_pole_lp(men, 1400)
    y = np.concatenate([ah, men])
    y *= fade(len(y), 0.04, 0.12)
    y *= strength
    return y

def _sapi_wav(text, out_path, rate=-3):
    """Windows SAPI。成功返回 True。"""
    if os.name != "nt":
        return False
    out_path = os.path.abspath(out_path)
    # PowerShell 里路径用单引号
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = {int(rate)}; $s.Volume = 100; "
        f"$s.SetOutputToWaveFile('{out_path}'); "
        f"$s.Speak('{text}'); $s.Dispose();"
    )
    try:
        r = _no_window_run(
            ["powershell", "-NoProfile", "-Command", ps],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        return r.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 1000
    except Exception:
        return False

def _read_wav_mono(path, target_sr=SR):
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    else:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
        data = (data - 128.0) / 128.0
    if nch > 1:
        data = data.reshape(-1, nch).mean(axis=1)
    if sr != target_sr and len(data) > 4:
        t_old = np.linspace(0, 1, len(data), endpoint=False)
        n_new = int(len(data) * target_sr / sr)
        t_new = np.linspace(0, 1, n_new, endpoint=False)
        data = np.interp(t_new, t_old, data)
    return data

def _pitch_shift_crude(x, ratio):
    if abs(ratio - 1.0) < 0.01:
        return x
    n_new = max(8, int(len(x) / ratio))
    t_old = np.linspace(0, 1, len(x), endpoint=False)
    t_new = np.linspace(0, 1, n_new, endpoint=False)
    return np.interp(t_new, t_old, x)

def synth_crowd_phrase(duration, kind="amen", strength=1.0, seed=21):
    rng = _rng(seed)
    n = int(SR * duration)
    out = np.zeros((n, 2), dtype=np.float64)

    phrases = {
        "amen": ["Amen", "Amen", "Aamen"],
        "strong": ["Amen!", "Yes Amen", "Amen"],
        "yes": ["Yes", "Yeah", "That's right"],
    }
    texts = phrases.get(kind, phrases["amen"])
    voices = []

    tmpdir = tempfile.mkdtemp(prefix="cutora_sfx_")
    try:
        for i, text in enumerate(texts):
            p = os.path.join(tmpdir, f"v{i}.wav")
            if _sapi_wav(text, p, rate=-4 + (i % 3)):
                try:
                    voices.append(_read_wav_mono(p))
                except Exception:
                    pass
    finally:
        try:
            for fn in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, fn))
            os.rmdir(tmpdir)
        except Exception:
            pass

    n_people = 14 if kind != "strong" else 20
    for i in range(n_people):
        f0 = float(rng.uniform(85, 175) if i % 4 != 0 else rng.uniform(170, 250))
        if voices:
            src = voices[i % len(voices)].copy()
            ratio = f0 / 140.0
            src = _pitch_shift_crude(src, ratio)
            src *= float(rng.uniform(0.35, 1.0)) * strength
        else:
            src = _synth_amen_one(rng.uniform(0.7, 1.2), f0, rng, strength * rng.uniform(0.4, 1.0))
        delay = int(rng.uniform(0.0, 0.38) * SR)
        if delay + len(src) > n:
            src = src[: max(1, n - delay)]
        pan = float(rng.uniform(-0.85, 0.85))
        sl = src * math.sqrt((1 - pan) * 0.5)
        sr_ = src * math.sqrt((1 + pan) * 0.5)
        end = delay + len(src)
        out[delay:end, 0] += sl
        out[delay:end, 1] += sr_

    out *= fade(n, 0.05, 0.25)[:, None]
    return reverb_tail(out, rt60=2.2)

# ── 自然环境 ──────────────────────────────────────────────────────────────
def synth_rain(duration, heavy=False, seed=31):
    rng = _rng(seed)
    n = int(SR * duration)
    noise = rng.standard_normal(n)
    hiss = one_pole_hp(noise, 1800 if heavy else 2800) * (0.35 if heavy else 0.22)
    drops_l = np.zeros(n)
    drops_r = np.zeros(n)
    n_drops = int(duration * (90 if heavy else 35))
    for _ in range(n_drops):
        t0 = int(rng.integers(0, n - 200))
        clen = int(rng.uniform(0.004, 0.018) * SR)
        d = rng.standard_normal(clen) * np.exp(-np.linspace(0, 14, clen)) * rng.uniform(0.08, 0.35)
        if rng.random() < 0.5:
            drops_l[t0:t0 + clen] += d
        else:
            drops_r[t0:t0 + clen] += d
    lfo = 0.85 + 0.15 * np.sin(2 * math.pi * 0.08 * np.arange(n) / SR)
    left = (hiss + drops_l) * lfo
    right = (hiss * 0.92 + drops_r + np.roll(hiss, 90) * 0.15) * lfo
    out = np.column_stack([left, right])
    out *= fade(n, 0.4, 0.5)[:, None]
    return out

def synth_waves(duration, seed=41):
    rng = _rng(seed)
    n = int(SR * duration)
    t = np.arange(n) / SR
    # 慢起伏：两个不同周期叠在一起
    surf = 0.45 + 0.35 * np.sin(2 * math.pi * 0.07 * t) + 0.2 * np.sin(2 * math.pi * 0.031 * t + 1.2)
    noise = one_pole_lp(pink_like(n, rng), 900)
    hiss = one_pole_hp(rng.standard_normal(n), 2500) * 0.12
    crash = np.zeros(n)
    t0 = 0.8
    while t0 < duration - 0.5:
        cdur = float(rng.uniform(1.4, 2.6))
        s0 = int(t0 * SR)
        cn = int(cdur * SR)
        if s0 + cn >= n:
            break
        env = np.sin(np.linspace(0, math.pi, cn)) ** 1.4
        band = one_pole_lp(rng.standard_normal(cn), 700) * env * rng.uniform(0.5, 1.0)
        crash[s0:s0 + cn] += band
        t0 += rng.uniform(2.4, 4.2)
    left = (noise * surf + hiss + crash) * 0.7
    right = (np.roll(noise, 220) * surf + np.roll(hiss, 40) + np.roll(crash, 160)) * 0.7
    out = np.column_stack([left, right])
    out *= fade(n, 0.5, 0.6)[:, None]
    return reverb_tail(out, rt60=1.1)

def synth_river(duration, seed=51):
    rng = _rng(seed)
    n = int(SR * duration)
    base = one_pole_lp(pink_like(n, rng), 1600) * 0.45
    gurgle = biquad_bp(rng.standard_normal(n), 520, 3.5) * 0.12
    sparkle = one_pole_hp(rng.standard_normal(n), 5000) * 0.06
    lfo = 0.8 + 0.2 * np.sin(2 * math.pi * 0.11 * np.arange(n) / SR)
    left = (base + gurgle + sparkle) * lfo
    right = (np.roll(base, 70) + np.roll(gurgle, 130) * 0.8 + sparkle * 0.9) * lfo
    out = np.column_stack([left, right])
    out *= fade(n, 0.4, 0.5)[:, None]
    return out

def synth_birds(duration, seed=61):
    rng = _rng(seed)
    n = int(SR * duration)
    out = np.zeros((n, 2), dtype=np.float64)
    # 轻风底
    air = one_pole_lp(pink_like(n, rng), 800) * 0.05
    out[:, 0] += air
    out[:, 1] += np.roll(air, 40)
    n_chirps = int(duration * 1.4)
    for _ in range(n_chirps):
        t0 = float(rng.uniform(0.2, duration - 0.35))
        d = float(rng.uniform(0.08, 0.28))
        nn = int(d * SR)
        s0 = int(t0 * SR)
        if s0 + nn >= n:
            continue
        f0 = float(rng.uniform(1800, 4200))
        f1 = f0 * float(rng.uniform(1.15, 1.7) if rng.random() > 0.4 else rng.uniform(0.7, 0.92))
        tt = np.arange(nn) / SR
        freq = np.linspace(f0, f1, nn)
        # 内部颤音
        freq += np.sin(2 * math.pi * rng.uniform(12, 28) * tt) * rng.uniform(40, 160)
        phase = np.cumsum(2 * math.pi * freq / SR)
        chirp = np.sin(phase) * np.sin(np.linspace(0, math.pi, nn)) ** 1.2
        chirp *= float(rng.uniform(0.08, 0.22))
        pan = float(rng.uniform(-0.9, 0.9))
        out[s0:s0 + nn] += to_stereo(chirp, pan)
    out *= fade(n, 0.3, 0.4)[:, None]
    return out

def synth_wind(duration, seed=71):
    rng = _rng(seed)
    n = int(SR * duration)
    t = np.arange(n) / SR
    noise = one_pole_lp(pink_like(n, rng), 500)
    gust = 0.55 + 0.45 * (0.5 + 0.5 * np.sin(2 * math.pi * 0.05 * t + 0.4)) ** 2
    whistle = np.sin(2 * math.pi * (380 + 40 * np.sin(2 * math.pi * 0.13 * t)) * t) * 0.03 * gust
    left = noise * gust * 0.5 + whistle
    right = np.roll(noise, 180) * gust * 0.5 + whistle * 0.7
    out = np.column_stack([left, right])
    out *= fade(n, 0.5, 0.6)[:, None]
    return out

def generate_library(out_dir=None):
    from cutora.audio.sfx_library import SFX_DIR
    out_dir = out_dir or SFX_DIR
    os.makedirs(out_dir, exist_ok=True)
    jobs = [
        ("crowd_applause_short.wav", lambda: synth_applause(5.0, 1.0, 160, seed=101)),
        ("crowd_applause_long.wav", lambda: synth_applause(8.0, 1.15, 200, seed=102)),
        ("crowd_applause_soft.wav", lambda: synth_applause(5.0, 0.55, 90, seed=103, whoops=False)),
        ("crowd_amen_soft.wav", lambda: synth_crowd_phrase(4.0, "amen", 0.75, seed=201)),
        ("crowd_amen_strong.wav", lambda: synth_crowd_phrase(5.0, "strong", 1.1, seed=202)),
        ("crowd_yes.wav", lambda: synth_crowd_phrase(3.2, "yes", 0.9, seed=203)),
        ("venue_ambience.wav", lambda: synth_venue(14.0, seed=301)),
        ("crowd_murmur.wav", lambda: synth_murmur(12.0, seed=302)),
        ("birds_forest.wav", lambda: synth_birds(12.0, seed=401)),
        ("ocean_waves.wav", lambda: synth_waves(12.0, seed=411)),
        ("river_flow.wav", lambda: synth_river(12.0, seed=421)),
        ("rain_light.wav", lambda: synth_rain(12.0, False, seed=431)),
        ("rain_heavy.wav", lambda: synth_rain(12.0, True, seed=432)),
        ("wind_soft.wav", lambda: synth_wind(10.0, seed=441)),
    ]
    print("[sfx] generating", len(jobs), "files →", out_dir)
    for name, fn in jobs:
        write_wav(os.path.join(out_dir, name), fn())
    print("[sfx] done")
    return out_dir

if __name__ == "__main__":
    generate_library()
