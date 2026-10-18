# Real-footage restore orchestrator. Usage: rr.py compare | rr.py full <variant> | rr.py upscale
import json, sys, subprocess, os, time, glob, urllib.request, uuid
R=r"D:\0_LLM\Outputs\restore_real"; IN=r"D:\0_LLM\ComfyUI\input"; OUT=r"D:\0_LLM\ComfyUI\output"
FF=glob.glob(r"D:\0_LLM\ComfyUI\venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg*.exe")[0]
PY=r"D:\0_LLM\ComfyUI\venv\Scripts\python.exe"; RUN=r"D:\0_LLM\bench\inpaint\run_api.py"
W,H=576,768; START=20.0; DUR=15.0; FPS=30
LOG=open(os.path.join(R,"rr.log"),"a",encoding="utf-8")
def L(m): LOG.write(time.strftime("%H:%M:%S ")+m+"\n"); LOG.flush(); print(m,flush=True)
def ff(*a): subprocess.run([FF,"-y","-loglevel","error",*a],check=True)
def vram():
    return int(subprocess.run(["nvidia-smi","--query-gpu=memory.used","--format=csv,noheader,nounits"],capture_output=True,text=True).stdout.strip())
def waitfree():
    for i in range(540):
        u=vram()
        if u<6000: return u
        if i%30==0: L(f"wait VRAM {u}")
        time.sleep(10)
    return vram()
def free():
    try: urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8188/free",data=b'{"unload_models":true,"free_memory":true}',headers={"Content-Type":"application/json"}),timeout=30)
    except Exception: pass
    time.sleep(10)
POS=("The same scene, restored to clean high-quality footage: an older woman with long blonde hair and black rectangular glasses talking to camera in a bright living room, "
     "sharp in-focus face with natural skin texture, identical facial features, expression and lip movement, patterned dark blouse, white walls and ceiling, "
     "windows in the background with blinds and sheer curtains, balanced exposure, soft natural indoor light, no blown-out highlights, natural colors, correct white balance. Photorealistic, high detail.")
NEG="blurry, soft focus, motion blur, noise, compression artifacts, overexposed, blown-out windows, washed out, color cast, different face, distorted face, deformed glasses, flicker, jitter, cartoon, worst quality"
def prompt(src, loras, prefix, seed=42):
    P={"unet":{"class_type":"UNETLoader","inputs":{"unet_name":"ltx-2.5-22b-distilled-transformer-nvfp4.safetensors","weight_dtype":"default"}}}
    prev=["unet",0]
    for i,(n,s) in enumerate(loras):
        P[f"l{i}"]={"class_type":"LTXICLoRALoaderModelOnly","inputs":{"lora_name":n,"strength_model":s,"model":prev}}; prev=[f"l{i}",0]
    P.update({
    "vae":{"class_type":"VAELoader","inputs":{"vae_name":"ltx-2.5-video-vae-bf16.safetensors"}},
    "avae":{"class_type":"VAELoader","inputs":{"vae_name":"ltx-2.5-audio-vae-bf16.safetensors"}},
    "clip":{"class_type":"CLIPLoader","inputs":{"clip_name":"gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors","type":"ltxv","device":"default"}},
    "lv":{"class_type":"LoadVideo","inputs":{"file":src}},
    "gvc":{"class_type":"GetVideoComponents","inputs":{"video":["lv",0]}},
    "size":{"class_type":"GetImageSize","inputs":{"image":["gvc",0]}},
    "pos":{"class_type":"CLIPTextEncode","inputs":{"text":POS,"clip":["clip",0]}},
    "neg":{"class_type":"CLIPTextEncode","inputs":{"text":NEG,"clip":["clip",0]}},
    "cond":{"class_type":"LTXVConditioning","inputs":{"frame_rate":["gvc",2],"positive":["pos",0],"negative":["neg",0]}},
    "empty":{"class_type":"EmptyLTXVLatentVideo","inputs":{"width":["size",0],"height":["size",1],"length":["size",2],"batch_size":1}},
    "guide":{"class_type":"LTXAddVideoICLoRAGuideAdvanced","inputs":{"frame_idx":0,"strength":1,"latent_downscale_factor":1,"crop":"disabled","use_tiled_encode":True,"tile_size":256,"tile_overlap":64,"attention_strength":1,"positive":["cond",0],"negative":["cond",1],"vae":["vae",0],"latent":["empty",0],"image":["gvc",0]}},
    "aenc":{"class_type":"VAEEncodeAudio","inputs":{"audio":["gvc",1],"vae":["avae",0]}},
    "aref":{"class_type":"LTXVSetAudioRefTokens","inputs":{"positive":["cond",0],"negative":["cond",1],"audio_latent":["aenc",0]}},
    "cat":{"class_type":"LTXVConcatAVLatent","inputs":{"video_latent":["guide",2],"audio_latent":["aref",2]}},
    "noise":{"class_type":"RandomNoise","inputs":{"noise_seed":seed}},
    "cfg":{"class_type":"CFGGuider","inputs":{"cfg":1,"model":prev,"positive":["guide",0],"negative":["guide",1]}},
    "ks":{"class_type":"KSamplerSelect","inputs":{"sampler_name":"euler_ancestral"}},
    "sig":{"class_type":"ManualSigmas","inputs":{"sigmas":"1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"}},
    "samp":{"class_type":"SamplerCustomAdvanced","inputs":{"noise":["noise",0],"guider":["cfg",0],"sampler":["ks",0],"sigmas":["sig",0],"latent_image":["cat",0]}},
    "sep":{"class_type":"LTXVSeparateAVLatent","inputs":{"av_latent":["samp",0]}},
    "crop":{"class_type":"LTXVCropGuides","inputs":{"positive":["guide",0],"negative":["guide",1],"latent":["sep",0]}},
    "dec":{"class_type":"VAEDecodeTiled","inputs":{"tile_size":512,"overlap":64,"temporal_size":64,"temporal_overlap":8,"samples":["crop",2],"vae":["vae",0]}},
    "cv":{"class_type":"CreateVideo","inputs":{"fps":["gvc",2],"bit_depth":8,"images":["dec",0]}},
    "save":{"class_type":"SaveVideo","inputs":{"filename_prefix":"restore_real/"+prefix,"format":"auto","format.codec":"auto","video":["cv",0]}}})
    return P
def run(P,tag):
    u=waitfree(); jp=os.path.join(R,f"api_{tag}.json"); json.dump(P,open(jp,"w"),indent=1)
    L(f"run {tag} vram_before={u}")
    o=subprocess.run([PY,RUN,jp,tag],capture_output=True,text=True).stdout.strip().splitlines()
    res=json.loads(o[-1]) if o and o[-1].startswith("{") else {"status":"?","raw":o[-3:]}
    L(f"{tag} status={res.get('status')} wall={res.get('wall_s')} peakVRAM={res.get('peak_vram_mib')} err={str(res.get('error',''))[:500]}")
    free()
    f=sorted(glob.glob(os.path.join(OUT,"restore_real",tag+"_*.mp4")),key=os.path.getmtime)
    return f[-1] if f else None
LR=("ltx-2.5-22b-ic-lora-restore-1.0.safetensors"); LD=("ltx-2.5-22b-ic-lora-deblur-0.9.safetensors")
VAR={"A_restore":[(LR,1.0)],"B_stack":[(LR,0.8),(LD,0.6)]}
def cut(name,ss,frames,src=os.path.join(R,"orig.mp4")):
    ff("-ss",f"{ss:.4f}","-i",src,"-vf",f"scale={W}:{H}:flags=lanczos,fps={FPS},setsar=1","-frames:v",str(frames),"-c:v","libx264","-crf","12","-pix_fmt","yuv420p","-c:a","aac","-ar","48000",os.path.join(IN,name))
mode=sys.argv[1]
if mode=="compare":
    cut("rr_cmp.mp4",START+5,49)
    outs={}
    for k,v in VAR.items(): outs[k]=run(prompt("rr_cmp.mp4",v,k),k)
    # C: two-pass deblur then restore
    d=outs["A_restore"]
    if d:
        import shutil; ff("-i",d,"-f","lavfi","-i","anullsrc=r=48000:cl=stereo","-map","0:v","-map","1:a","-c:v","libx264","-crf","12","-pix_fmt","yuv420p","-c:a","aac","-shortest",os.path.join(IN,"rr_cmp_res.mp4"))
        outs["C_restore_then_deblur"]=run(prompt("rr_cmp_res.mp4",[(LD,1.0)],"C_restore_then_deblur"),"C_restore_then_deblur")
    ins=[os.path.join(IN,"rr_cmp.mp4")]+[outs[k] for k in ["A_restore","B_stack","C_restore_then_deblur"] if outs.get(k)]
    a=[];fc=""
    for i,p in enumerate(ins): a+=["-i",p]; fc+=f"[{i}:v]scale={W}:{H},setsar=1[v{i}];"
    fc+="".join(f"[v{i}]" for i in range(len(ins)))+f"hstack=inputs={len(ins)}[v]"
    ff(*a,"-filter_complex",fc,"-map","[v]","-an","-c:v","libx264","-crf","18","-pix_fmt","yuv420p",os.path.join(R,"compare_orig_A_B_C.mp4"))
    L("COMPARE_DONE "+json.dumps(outs))
if mode=="full":
    # sys.argv[2]: comma list of pass specs, e.g. "R1.0;D1.0" two passes, or "R0.8+D0.6" stacked
    spec=sys.argv[2]; CH=97; ST=81; N=int(DUR*FPS)
    starts=list(range(0,N-CH+1,ST));
    if starts[-1]+CH<N: starts.append(N-CH)
    ff("-ss",f"{START}","-i",os.path.join(R,"orig.mp4"),"-t",f"{DUR}","-vf",f"scale={W}:{H}:flags=lanczos,fps={FPS},setsar=1","-c:v","libx264","-crf","12","-pix_fmt","yuv420p","-c:a","aac","-ar","48000",os.path.join(R,"seg_in.mp4"))
    ff("-ss",f"{START}","-i",os.path.join(R,"orig.mp4"),"-t",f"{DUR}","-c:v","libx264","-crf","14","-pix_fmt","yuv420p","-c:a","aac",os.path.join(R,"orig_15s.mp4"))
    t0=time.time(); outs=[]
    for ci,s0 in enumerate(starts):
        cur=f"rr_c{ci}.mp4"; ff("-ss",f"{s0/FPS:.4f}","-i",os.path.join(R,"seg_in.mp4"),"-frames:v",str(CH),"-c:v","libx264","-crf","12","-pix_fmt","yuv420p","-c:a","aac",os.path.join(IN,cur))
        for pi,p in enumerate(spec.split(";")):
            loras=[(LR if x[0]=="R" else LD,float(x[1:])) for x in p.split("+")]
            tag=f"full_c{ci}_p{pi}"; o=run(prompt(cur,loras,tag),tag)
            if not o: L("CHUNK FAIL"); sys.exit(1)
            cur=f"rr_c{ci}_p{pi}.mp4"; ff("-i",o,"-f","lavfi","-i","anullsrc=r=48000:cl=stereo","-map","0:v","-map","1:a","-shortest","-c:v","libx264","-crf","12","-pix_fmt","yuv420p","-c:a","aac",os.path.join(IN,cur))
        outs.append(os.path.join(IN,cur))
    L(f"chunks done {len(starts)} in {time.time()-t0:.0f}s starts={starts}")
    import cv2, numpy as np
    def rd(p):
        c=cv2.VideoCapture(p); fr=[]
        while True:
            ok,f=c.read()
            if not ok: break
            fr.append(f.astype(np.float32))
        return fr
    acc=[None]*N; wsum=np.zeros(N)
    for s0,p in zip(starts,outs):
        fr=rd(p)[:CH]; n=len(fr)
        for i,f in enumerate(fr):
            w=min(1.0,(i+1)/(CH-ST+1),(n-i)/(CH-ST+1))  # linear crossfade over overlap
            if s0==0 and i<CH-ST: w=1.0
            if s0+n>=N and i>=n-(CH-ST): w=max(w,1.0)
            k=s0+i
            if k<N: acc[k]=f*w if acc[k] is None else acc[k]+f*w; wsum[k]+=w
    vw=cv2.VideoWriter(os.path.join(R,"restored_raw.avi"),cv2.VideoWriter_fourcc(*"MJPG"),FPS,(W,H))
    for k in range(N):
        if acc[k] is not None: vw.write(np.clip(acc[k]/wsum[k],0,255).astype(np.uint8))
    vw.release()
    ff("-i",os.path.join(R,"restored_raw.avi"),"-i",os.path.join(R,"orig_15s.mp4"),"-map","0:v","-map","1:a","-c:v","libx264","-crf","14","-pix_fmt","yuv420p","-c:a","copy","-shortest",os.path.join(R,"restored_576.mp4"))
    L("FULL_DONE "+spec)
