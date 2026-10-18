import cv2, glob, subprocess, numpy as np, sys
FF = glob.glob(r"D:\0_LLM\ComfyUI\venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg*.exe")[0]
src = glob.glob(r"D:\0_LLM\ComfyUI\output\ltx25_test\i2v_576x1024_15s_0000*.mp4")[0]
clip = r"D:\0_LLM\ComfyUI\input\inpaint_src_5s.mp4"; mask = r"D:\0_LLM\ComfyUI\input\inpaint_mask_5s.mp4"
subprocess.run([FF,"-y","-i",src,"-t","5.041","-frames:v","121","-c:v","libx264","-crf","14","-pix_fmt","yuv420p","-c:a","aac",clip],check=True,capture_output=True)
cap = cv2.VideoCapture(clip); fd = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
frames = []; last = None; hits = 0
while True:
    ok, f = cap.read()
    if not ok: break
    g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY); fs = fd.detectMultiScale(g, 1.1, 5, minSize=(60, 60))
    if len(fs): last = max(fs, key=lambda r: r[2]*r[3]); hits += 1
    m = np.zeros(f.shape[:2], np.uint8)
    if last is not None:
        x, y, w, h = last; cv2.ellipse(m, (int(x+w/2), int(y+h*0.78)), (int(w*0.30), int(h*0.17)), 0, 0, 360, 255, -1)
    frames.append(m)
h, w = frames[0].shape
p = subprocess.Popen([FF,"-y","-f","rawvideo","-pix_fmt","gray","-s",f"{w}x{h}","-r","24","-i","-","-c:v","libx264","-crf","0","-pix_fmt","yuv420p",mask],stdin=subprocess.PIPE,stderr=subprocess.DEVNULL)
for m in frames: p.stdin.write(cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)[:, :, 0].tobytes())
p.stdin.close(); p.wait()
cv2.imwrite(r"D:\0_LLM\bench\inpaint\mask_preview.png", np.hstack([cv2.cvtColor(frames[60],cv2.COLOR_GRAY2BGR)]))
print("frames", len(frames), "face_hits", hits, "size", w, h)
