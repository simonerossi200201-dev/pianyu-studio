# LTX-2.5 SDR-to-HDR IC-LoRA – notes (official model card + official ComfyUI-LTXVideo workflow)
Source workflow: ComfyUI\custom_nodes\ComfyUI-LTXVideo\example_workflows\2.5\HDR_workflows\LTX-2.5_ICLoRA_SDR_to_HDR_Distilled.json
API version: api_sdr2hdr.json (flattened from that workflow; only change: nvfp4 transformer instead of bf16, which is not on disk)
Files: lora -> models\loras ; scene-emb -> models\embeddings (loaded by LTXVLoadConditioning; NO prompt, NO text encoder)
Key rules: LoRA strength fixed 1.0; 8 Euler steps, ManualSigmas distilled, CFG 1; frame count must be 8k+1;
 runs at source res (resized to multiple of 32); conditioning fps capped at 30; input colorspace srgb_gamma for normal MP4.
 Card: OOM tends to happen in VAE decode -> use shorter trims. Reproducibility depends on free VRAM (tiling auto).
Outputs:
 - ltx_hdr/sdr_tonemapped*.mp4 : Reinhard tone-mapped SDR preview (this is the "back to SDR" output; for a better SDR, grade the EXR/HLG)
 - ltx_hdr/hlg*.mp4 : BT.2020 HLG 10-bit HEVC master
 - EXR (ACEScg linear) only if post.save_exr=true -> requires `openimageio` in ComfyUI venv (NOT installed yet).
 Alternative tone-map back to SDR: ffmpeg zscale/tonemap on the HLG master, e.g.
 ffmpeg -i hlg.mp4 -vf "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p" sdr.mp4
 (needs ffmpeg built with zimg).
Use: copy clip to ComfyUI\input\hdr_in.mp4 (trim to 8k+1 frames, e.g. 49/97/121), POST {"prompt": api} to http://127.0.0.1:8188/prompt.
Note: the ComfyUI workflow has no seam-keyframes (those are only in the standalone ltx_pipelines.hdr_ic_lora CLI, not installed).
Blown windows: model expands highlight range; fully clipped white has no data and will not be reconstructed reliably.
