import subprocess, sys, glob, os
PY = r"D:\0_LLM\ComfyUI\venv\Scripts\python.exe"; R = r"D:\0_LLM\audio\runners\sao_run.py"
FF = glob.glob(r"D:\0_LLM\ComfyUI\venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg*.exe")[0]
OUT = r"D:\0_LLM\Outputs\audience_sfx"; os.makedirs(OUT, exist_ok=True); LOG = open(r"D:\0_LLM\logs\audience_sfx.log", "a", encoding="utf-8")
neg = "music, singing, speech, distorted, low quality"
J = [("crowd_murmur_20s", 20, "indoor auditorium crowd ambience, audience murmur and quiet chatter before a talk, large hall reverb, no music"),
     ("applause_enthusiastic_10s", 10, "enthusiastic loud applause from a large audience in an auditorium, clapping, field recording"),
     ("applause_polite_8s", 8, "light polite applause from a small audience in a conference room, soft clapping"),
     ("applause_cheering_10s", 10, "audience applause with some cheering and whistles in a large auditorium, excited crowd, no music")]
for name, sec, p in J:
    for seed in (1, 2):
        n = f"{name}_s{seed}"
        r = subprocess.run([PY, R, "--prompt", p, "--neg", neg, "--seconds", str(sec), "--seed", str(seed), "--out-name", "sfx_" + n], capture_output=True, text=True)
        line = [l for l in r.stdout.splitlines() if l.startswith("RESULT")]; LOG.write(n + " " + (line[0] if line else r.stdout[-500:] + r.stderr[-1500:]) + "\n"); LOG.flush()
        src = os.path.join(r"D:\0_LLM\Outputs\audio_test", "sfx_" + n + ".flac")
        if os.path.exists(src):
            for ext, codec in ((".wav", ["-c:a", "pcm_s16le"]), (".mp3", ["-c:a", "libmp3lame", "-b:a", "192k"])):
                subprocess.run([FF, "-y", "-loglevel", "error", "-i", src, "-t", str(sec), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", *codec, os.path.join(OUT, n + ext)], check=False)
LOG.write("ALL DONE\n")
