# -*- coding: utf-8 -*-
"""Flow 接口冒烟测试（真的会花额度，Lite 很便宜）。

    python tools\test_flow_api.py <账号名> <图片路径> ["提示词"] [4|6|8]
        没给提示词就只测：连接 → 上传 → 查媒体 → 生一张图；给了就再提交一段视频并等出片下载到 data\tmp。
"""
import sys, time, io
from pathlib import Path
if "pytest" not in sys.modules:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orante import db, paths, accounts
from orante.engines.flow_api import FlowEngine

def main():
    db.init()
    name = sys.argv[1]; img = Path(sys.argv[2])
    if sys.argv[2] == "auto":
        img = paths.find_legacy_root() / "0-长文案" / "数据" / "LUCA-1" / "0905" / "Tik022" / "图片" / "Tik022-p08.jpeg"
    prompt = sys.argv[3] if len(sys.argv) > 3 else ""
    if prompt.startswith("@"): prompt = Path(prompt[1:]).read_text(encoding="utf-8")
    dur = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    acc = next(a for a in accounts.list_all() if a["name"] == name)
    eng = FlowEngine(acc, print)
    eng.connect()
    print("health", eng.health())
    if sys.argv[2] == "poll":           # python tools\test_flow_api.py TIK-L1 poll <genId>
        gid = sys.argv[3]
        r = eng.poll([gid]); print("poll", r)
        print("media", eng.media_urls(gid))
        if r[gid]["state"] == "done":
            print("saved", eng.download(gid, paths.DATA / "tmp" / "test-poll.mp4"))
        eng.close(); return
    mid = eng.upload_image(img); print("mediaId", mid)
    print("urls", eng.media_urls(mid))
    imgs = eng.gen_images("同一个人物，同样的服装和姿态，只改变拍摄角度：近景侧面。", img, n=1, dest_dir=paths.DATA / "tmp")
    print("gen_images", imgs)
    if prompt:
        gid = eng.submit({"s": 1}, img, prompt, dur); print("genId", gid)
        t0 = time.time()
        while time.time() - t0 < 900:
            r = eng.poll([gid]); print(" poll", r); 
            if r[gid]["state"] != "pending": break
            time.sleep(8)
        if r[gid]["state"] == "done":
            print("saved", eng.download(gid, paths.DATA / "tmp" / f"test-{dur}s.mp4"))
    eng.close()

if __name__ == "__main__":
    main()
