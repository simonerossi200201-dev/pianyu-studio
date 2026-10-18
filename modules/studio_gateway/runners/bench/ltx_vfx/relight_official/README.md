---
base_model:
- Lightricks/LTX-2.3
base_model_relation: adapter
license: other
license_name: ltx-2-community-license
license_link: https://github.com/Lightricks/LTX-2/blob/main/LICENSE
language:
- en
tags:
- ltx-video
- ic-lora
- ltx-2.3
- video-to-video
- relight
- lighting
- diffusion-single-file
- ltx
pipeline_tag: video-to-video
widget:
- text: >-
    relight the video to match the light-direction ball. hard low-angle sunlight
    from the right
  output:
    url: examples/01_ocean_output_lora_sbs.mp4
- text: >-
    relight the video to match the light-direction ball. hard low-angle sunlight
    from the right
  output:
    url: examples/03_lighthouse_output_lora_sbs.mp4
- text: >-
    relight the video to match the light-direction ball. warm golden low side
    sun from the left
  output:
    url: examples/10_snow_ridge_output_lora_sbs.mp4
extra_gated_description: >-
  By clicking "Agree and Access" you acknowledge the [Privacy
  Policy](https://static.lightricks.com/legal/Privacy%20Policy%20-%20LTX%20Platform.pdf) 
  and consent to receive offers and updates including targeted and personalized
  advertisements. You can unsubscribe at any time.
extra_gated_button_content: Agree and Access
---

# LTX-2.3 22B IC-LoRA Relight (Sun Direction)

This is an **IC-LoRA** trained on top of **LTX-2.3-22B** that relights an exterior video to a chosen sun direction and lighting hardness, driven by a small "light-direction ball" composited into the reference video's corner.

It is based on the [LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3) foundation model.

<Gallery />

## Model Files

`ltx-2.3-22b-ic-lora-relight-1.0.safetensors`

## Model Details

- **Base Model:** LTX-2.3-22B Video
- **Training Type:** IC-LoRA
- **Control Type:** Video reference (source clip + light-direction ball)
- **Reference Downscale Factor:** 1 (the reference is at the same resolution as the output)
- **Pipeline details:** no external models or per-frame relighting at inference; a single-stage pass at the target resolution.

## Intended Use & Out-of-Scope

**Intended use:** relighting **exterior** video clips to a chosen sun direction and hardness, using the base LTX-2.3 pipeline in ComfyUI or the LTX Python pipelines.

**Out of scope:** interior footage (the model was trained on exteriors only); scene/content editing (it relights, it does not restyle content); resolutions and clip lengths well beyond the tested ranges.

## Control Signal Requirements

- **Control signal type:** light-direction reference — the source clip with a direction sphere composited into it.
- **Expected input:** the source **video**, with the direction ball composited into the **top-right corner** on every frame.
- **Preprocessing:** render the direction sphere (e.g. Eric Venti's Sphere-Light-Render ComfyUI node) and composite it into the corner at the training geometry — ball ≈ **143 px** diameter, **22 px** margin at a **1280×704** frame. Outside ComfyUI this is a manual step — see [**Building the reference video**](#-building-the-reference-video-only-outside-comfyui).
- **Alignment:** the reference must match the output in frame count, FPS, resolution and aspect ratio.
- **Mask support:** none.

## How It Works

The model conditions on a reference video that is the **source clip with a light-direction ball in the corner**, plus a short caption. It outputs the same clip relit so the key light matches the ball's direction and the caption's look — end-to-end, with no separate relighting model at inference.

## Usage

### 🔌 ComfyUI

A ready-to-run graph is included: [`LTX-2.3_Relight_ICLoRA_SingleStage_Distilled.json`](LTX-2.3_Relight_ICLoRA_SingleStage_Distilled.json).

1. Copy `ltx-2.3-22b-ic-lora-relight-1.0.safetensors` into `models/loras`.
2. Open the workflow. It wires the **LTX-2.3-22B distilled** base, the IC-LoRA loader (`ltx-2.3-22b-ic-lora-relight-1.0.safetensors` at strength `1.0`), Eric Venti's **Sphere-Light-Render** node, and in-graph compositing of the ball onto your source frames — so you don't have to pre-composite anything.
3. Load your **source video** in the `LoadVideo` node.
4. Set the light **direction** on the Sphere-Light-Render node (rotation / elevation / intensity), and write the prompt (see **Recommended Settings**).
5. Run. The graph relights single-stage at the target resolution (1280×704).

### 🔦 Building the reference video (only outside ComfyUI)

**If you use the ComfyUI graph above, skip this section.** That graph contains Eric Venti's **Sphere-Light-Render** node, which renders the light-direction ball from your chosen rotation/elevation and blends it onto your source frames *in-graph* — there is nothing to prepare by hand.

Every other path (the Python pipeline below, or any custom integration) has no such node, so **you must build the reference video yourself**. The model does not accept a direction as a parameter — the ball composited into the frame *is* the control signal, so a source clip without it will not steer the lighting.

**What you are building:** your source clip with a small light-direction ball patch blended over the **top-right corner of every frame**. The ball encodes the sun direction: a sphere lit from the target direction, with its shadow cast in the opposite direction.

**Geometry** (measured on the shipped examples, at the trained `1280×704`):

| Property | Value |
|---|---|
| Patch size | `143 × 143` px square |
| Position | `22` px from the **top** edge, `22` px from the **right** edge |
| Patch background | flat neutral grey, ≈ `RGB(173, 173, 173)` |
| Sphere | centred in the patch's upper half, ≈ ⅓ of the patch width |

Here is a real reference frame from the Ocean example — source clip plus the ball patch, exactly what the pipeline expects as `--video-conditioning`:

![Reference frame with the light-direction ball composited into the top-right corner](examples/reference_frame_with_ball.png)

**Steps:**

1. **Standardize the source clip** to the trained geometry — `1280×704`, `121` frames, `24` fps.
2. **Render the ball.** Use the [Sphere-Light-Render node](https://github.com/eric-venti-seeds/Sphere-Light-Render-ComfyUI) and save its output, or render your own: a grey square, a matte sphere lit from the direction you want, and a shadow tail pointing away from the light. Export as `ball.png`.
3. **Blend it onto every frame.** The direction is fixed for the whole clip, so the same patch is composited on all frames:

   ```bash
   ffmpeg -i source.mp4 -i ball.png \
     -filter_complex "[0:v]scale=1280:704,setsar=1[v];\
                      [1:v]scale=143:143[b];\
                      [v][b]overlay=W-w-22:22" \
     -r 24 -c:v libx264 -crf 18 -pix_fmt yuv420p \
     source_plus_ball.mp4
   ```

   `overlay=W-w-22:22` is what places the patch 22 px in from the top-right corner.
4. **Pass the result** as `--video-conditioning source_plus_ball.mp4` — see the pipeline call below.

**Getting this wrong is the most common failure.** If the ball is missing, cropped by a later resize, placed in a different corner, or absent from some frames, the model has no direction to follow and the relight will be weak or inconsistent. Composite the ball **last**, after every scaling step, and confirm it survives your final encode.

### 🐍 LTX Python pipeline

The LoRA runs in the LTX-2 `ic_lora` pipeline. Prepare a **reference video** as described above — your source clip with the direction ball composited into the top-right corner (same convention as the ComfyUI graph) — then run a single stage at the target size:

```bash
python -m ltx_pipelines.ic_lora \
  --distilled-checkpoint-path ltx-2.3-22b-distilled.safetensors \
  --spatial-upsampler-path    ltx2-spatial-upscaler-x2-1.0.bf16.safetensors \
  --gemma-root                <gemma-text-encoder-dir> \
  --lora ltx-2.3-22b-ic-lora-relight-1.0.safetensors 1.0 \
  --video-conditioning source_plus_ball.mp4 1.0 \
  --prompt "relight the video to match the light-direction ball. hard directional sunlight from the right" \
  --width 2560 --height 1408 \
  --num-frames 121 --frame-rate 24 --seed 42 \
  --output-path relit.mp4 --skip-stage-2
```

The key trick: request **2× the target resolution** and pass `--skip-stage-2`, so Stage 1's output *is* your target (here `2560×1408 → 1280×704`).

## Recommended Settings

- **LoRA strength / weight:** `1.0`
- **Resolution & frames:** trained at **1280×704 × 121 frames @ 24 fps** (the sweet spot); higher resolutions work at reduced clip length.
- **Prompting:** minimal caption — a fixed trigger, then one look phrase and a direction phrase. Do not describe the scene content.

  **Format:**
  ```
  relight the video to match the light-direction ball. <look> from <direction>
  ```
  - `<direction>` = the front / front-right / right / back-right / behind / back-left / left / front-left
  - `<look>` = one of the 12 trained looks:

    ```
    hard directional sunlight        hard high-angle sunlight       hard low-angle sunlight
    soft diffused daylight           soft warm afternoon light      cool soft daylight
    dim overcast light               strong backlight with rim light  soft hazy backlight
    warm golden low front sun        warm golden low side sun       frontal sunlight
    ```

  **Examples:**
  ```
  relight the video to match the light-direction ball. hard directional sunlight from the right
  relight the video to match the light-direction ball. warm golden low front sun from the front
  relight the video to match the light-direction ball. strong backlight with rim light from behind
  ```

### Direction Mapping

The ball's `rotation` (snapped to the nearest 45°) sets the direction phrase; its `elevation` sets the altitude band.

![Light-direction convention: how the ball's rotation and elevation map to sun position](sphere_convention.png)

| rotation | 0° | 45° | 90° | 135° | 180° | 225° | 270° | 315° |
|---|---|---|---|---|---|---|---|---|
| phrase | the front | the front-right | the right | the back-right | behind | the back-left | the left | the front-left |

Altitude band from `elevation`: **< 33°** = low sun · **33–57°** = mid sun · **≥ 58°** = high sun. Examples: `rot 90, el 45 → "the right, mid sun"`; `rot 180, el 22 → "behind, low sun"`.

The 12 trained looks, each shown on a reference sphere:

![The 12 trained light looks, each shown on a reference sphere](sphere_looks.png)

## Tips & Troubleshooting

- **Exterior only** — the relight was trained on exteriors; interiors are not supported.
- **Use single-stage** — the true 2-stage path is unusable for this reference LoRA (Stage 2 drops the reference and LoRA). Request the target size directly in a single stage.
- **Match the caption to the ball** — a caption that contradicts the ball's direction fights the reference; the model follows the ball.
- **Direction coverage** — backlit looks bias toward "behind"; pick a direction that is distinct from the scene's own lighting for the clearest effect.

## Dataset

The model was trained using a proprietary dataset of paired before/after relit video clips.

## Training

- **Technique:** IC-LoRA (rank 32, alpha 32) on the DiT transformer
- **Hyperparameters:** bf16, learning rate 2e-4 (linear), gradient checkpointing, batch size 1
- **Steps:** 3,000 (recommended checkpoint; checkpoints saved every 500 steps)
- **Infrastructure:** LTX-2 Community Trainer

## License

See the **[LTX-2-community-license](https://github.com/Lightricks/LTX-2/blob/main/LICENSE)** for full terms.

## Acknowledgments
- Base model by **[Lightricks](https://ltx.io/)**
- Training infrastructure: **[LTX-2 Community Trainer](https://github.com/Lightricks/LTX-2/tree/main/packages/ltx-trainer)**
- The light-direction-ball convention and the exterior sun-direction relighting idea follow **Eric Venti's** [Sun-Direction LoRA for FLUX.2 Klein 9B](https://huggingface.co/eric-venti-seeds/Sun-Direction-Lora-Flux2Klein9B) and the accompanying [Sphere-Light-Render ComfyUI node](https://github.com/eric-venti-seeds/Sphere-Light-Render-ComfyUI), used for the direction sphere at inference.