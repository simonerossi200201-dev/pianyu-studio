import torch, numpy as np, cv2, sys
from transformers import Sam2VideoModel, Sam2VideoProcessor
M=r"D:\0_Dev\CUTORA\runtime\models\matte_sam21_large"
src=r"D:\0_LLM\ComfyUI\input\vfx_clean_in.mp4"; out=r"D:\0_LLM\ComfyUI\input\mic_mask.mp4"
cap=cv2.VideoCapture(src); fr=[]
while True:
    ok,f=cap.read()
    if not ok: break
    fr.append(cv2.cvtColor(f,cv2.COLOR_BGR2RGB))
frames=np.stack(fr)
dev="cuda"
model=Sam2VideoModel.from_pretrained(M).to(dev, dtype=torch.bfloat16); proc=Sam2VideoProcessor.from_pretrained(M)
sess=proc.init_video_session(video=list(frames), inference_device=dev, dtype=torch.bfloat16)
proc.add_inputs_to_inference_session(inference_session=sess, frame_idx=0, obj_ids=1,
    input_points=[[[[265,395],[255,450],[238,530],[300,330]]]], input_labels=[[[1,1,1,0]]])
_=model(inference_session=sess, frame_idx=0)
masks={}
for o in model.propagate_in_video_iterator(sess):
    m=proc.post_process_masks([o.pred_masks],original_sizes=[[frames.shape[1],frames.shape[2]]],binarize=True)[0]
    masks[o.frame_idx]=m[0,0].cpu().numpy().astype(np.uint8)*255
arr=np.stack([np.repeat(masks[i][...,None],3,2) for i in range(len(frames))])
print("frames",len(frames),"mask area % per frame min/max", round(arr[...,0].mean(axis=(1,2)).min()/2.55,2), round(arr[...,0].mean(axis=(1,2)).max()/2.55,2))
vw=cv2.VideoWriter(out.replace(".mp4","_raw.avi"),cv2.VideoWriter_fourcc(*"MJPG"),25,(arr.shape[2],arr.shape[1]))
for a in arr: vw.write(a)
vw.release()
print("MASK_OK")

