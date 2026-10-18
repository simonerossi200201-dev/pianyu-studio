# LTX-2.3 IC-LoRA Relight - notes (2026-10-13)
Source: https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-Relight (commit 65eeff0e21fc139b30833a284fbd74e0a770a76d)
License: LTX-2 Community License (same as other LTX LoRAs already in use).
File: ltx-2.3-22b-ic-lora-relight-1.0.safetensors (338 MB) -> D:\0_LLM\Models_Cache\ltx25\loras + hardlink ComfyUI\models\loras
Log: D:\0_LLM\logs\hf_gated_relight.log (size + SHA256 vs HF LFS oid). Official workflow/sphere images -> D:\0_LLM\bench\ltx_vfx\relight_official\

## Compatibility (IMPORTANT)
- Official docs (docs.ltx.io/.../relight): "validated on LTX-2.3; NOT yet validated on LTX-2.5 (in development)".
- Official stack: ltx-2.3-22b-dev (or distilled) + ltx-2.3-22b-distilled-lora-384-1.1 + gemma-3-12b text encoder, LoRA strength 1.0, single stage 1280x704.
- Installed on LUCA: only LTX-2.5 (ltx-2.5-22b-distilled-transformer-nvfp4 + gemma4-12b LTX-2.5 encoder). No LTX-2.3 base.
- LTX blog: most 2.3 IC-LoRAs run on 2.5 unchanged, but Relight is explicitly flagged as not validated -> treat 2.5 runs as EXPERIMENTAL.
- The official graph needs the `SphereLightNode` (renders/composites the light-direction ball). It is NOT present in the installed
  ComfyUI-LTXVideo (commit 3bf3ca62, 2026-10-01). Getting it = updating that node pack -> needs Simone's OK first (not done).
- Workaround without node update: composite the ball yourself (sphere_convention.png / reference_frame_with_ball.png from the repo)
  into the top-right corner of the reference video with ffmpeg, then use the existing LTX-2.5 IC-LoRA graph (same as Clean Plate)
  with this LoRA at strength 1.0.
- Exterior scenes only (sun direction + hardness). Does not relight interiors.

## Prompt pattern (from card)
"relight the video to match the light-direction ball. hard low-angle sunlight from the right"
"relight the video to match the light-direction ball. warm golden low side sun from the left"

## Options to run validated
A) Download LTX-2.3 22B stack (~40+ GB) + update ComfyUI-LTXVideo for SphereLightNode (approval needed).
B) Experimental on LTX-2.5: workflow relight_ltx25_api.json (manual ball), validate visually.
