# SPDX-License-Identifier: GPL-3.0-or-later
"""
audio_spectrum.py  –  Generate per-frame audio spectrum data for visualization.

Uses numpy + wave/pydub for audio analysis. Falls back gracefully if
dependencies are missing (returns empty spectrum).
"""

import os, math, struct, wave

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# Number of frequency bands for the spectrum display
DEFAULT_BANDS = 32
# Cache: { audio_path: { "samples": np.array, "rate": int, "duration": float } }
_AUDIO_CACHE = {}

def _load_audio_pcm(audio_path):
    """Load audio file as mono PCM samples. Returns (samples_array, sample_rate) or (None, 0)."""
    if not os.path.isfile(audio_path):
        return None, 0

    ext = os.path.splitext(audio_path)[1].lower()

    # Try WAV first (no dependency)
    if ext == '.wav':
        try:
            with wave.open(audio_path, 'rb') as wf:
                n_channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                rate = wf.getframerate()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)

                if HAS_NUMPY:
                    if sampwidth == 2:
                        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    elif sampwidth == 4:
                        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
                    else:
                        samples = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0

                    if n_channels > 1:
                        samples = samples.reshape(-1, n_channels).mean(axis=1)
                    return samples, rate
                else:
                    # Without numpy, use array module for basic support
                    if sampwidth == 2:
                        fmt = f'<{n_frames * n_channels}h'
                        int_samples = struct.unpack(fmt, raw)
                        float_samples = [s / 32768.0 for s in int_samples]
                    else:
                        return None, 0

                    # Mono mix
                    if n_channels > 1:
                        mono = []
                        for i in range(0, len(float_samples), n_channels):
                            mono.append(sum(float_samples[i:i + n_channels]) / n_channels)
                        float_samples = mono
                    return float_samples, rate
        except Exception:
            return None, 0

    # For other formats, try pydub
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(audio_path)
        audio = audio.set_channels(1)  # mono
        rate = audio.frame_rate
        raw = audio.raw_data
        if audio.sample_width == 2:
            if HAS_NUMPY:
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            else:
                fmt = f'<{len(raw) // 2}h'
                int_s = struct.unpack(fmt, raw)
                samples = [s / 32768.0 for s in int_s]
        else:
            return None, 0
        return samples, rate
    except Exception:
        return None, 0

def _ensure_cached(audio_path):
    """Load and cache audio data."""
    if audio_path in _AUDIO_CACHE:
        return _AUDIO_CACHE[audio_path]

    samples, rate = _load_audio_pcm(audio_path)
    if samples is None or rate == 0:
        _AUDIO_CACHE[audio_path] = None
        return None

    if HAS_NUMPY:
        duration = len(samples) / rate
    else:
        duration = len(samples) / rate

    entry = {"samples": samples, "rate": rate, "duration": duration}
    _AUDIO_CACHE[audio_path] = entry
    return entry

def get_spectrum_at_time(audio_path, current_time, n_bands=DEFAULT_BANDS,
                         window_ms=50):
    """
    Return a list of `n_bands` float values in [0.0, 1.0] representing
    the audio spectrum magnitude at the given time.

    Parameters
    ----------
    audio_path  : str   – path to audio file
    current_time: float – seconds
    n_bands     : int   – number of frequency bands
    window_ms   : int   – FFT window size in milliseconds

    Returns
    -------
    list[float] of length n_bands, each in [0.0, 1.0]
    """
    entry = _ensure_cached(audio_path)
    if entry is None:
        return [0.0] * n_bands

    samples = entry["samples"]
    rate = entry["rate"]
    duration = entry["duration"]

    if current_time < 0 or current_time > duration:
        return [0.0] * n_bands

    # Window parameters
    window_samples = int(rate * window_ms / 1000.0)
    center_sample = int(current_time * rate)
    start = max(0, center_sample - window_samples // 2)

    if HAS_NUMPY:
        end = min(len(samples), start + window_samples)
        chunk = samples[start:end]
        if len(chunk) < 16:
            return [0.0] * n_bands

        # Apply Hanning window
        window = np.hanning(len(chunk))
        chunk = chunk * window

        # FFT
        fft_data = np.abs(np.fft.rfft(chunk))
        fft_len = len(fft_data)

        # Divide into bands (logarithmic scale for more natural feel)
        bands = []
        for i in range(n_bands):
            # Log scale: lower bands get fewer bins, higher get more
            low = int(fft_len * (2 ** (i / n_bands) - 1) / (2 - 1))
            high = int(fft_len * (2 ** ((i + 1) / n_bands) - 1) / (2 - 1))
            low = max(0, min(low, fft_len - 1))
            high = max(low + 1, min(high, fft_len))
            band_val = np.mean(fft_data[low:high]) if high > low else 0.0
            bands.append(float(band_val))

        # Normalize to [0, 1]
        max_val = max(bands) if bands else 1.0
        if max_val > 0:
            bands = [min(1.0, b / max_val) for b in bands]
        return bands
    else:
        # Without numpy: very basic amplitude-based bars (no FFT)
        end = min(len(samples), start + window_samples)
        chunk = samples[start:end] if isinstance(samples, list) else list(samples[start:end])
        if len(chunk) < 4:
            return [0.0] * n_bands

        # Divide chunk into n_bands segments and compute RMS
        seg_size = max(1, len(chunk) // n_bands)
        bands = []
        for i in range(n_bands):
            seg = chunk[i * seg_size: (i + 1) * seg_size]
            if seg:
                rms = math.sqrt(sum(s * s for s in seg) / len(seg))
                bands.append(min(1.0, rms * 3.0))  # scale up for visibility
            else:
                bands.append(0.0)
        return bands

def clear_cache():
    """Clear the audio cache."""
    _AUDIO_CACHE.clear()
