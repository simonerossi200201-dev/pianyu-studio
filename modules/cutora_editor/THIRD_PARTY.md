# THIRD_PARTY.md — CUTORA / 片语 Studio

CUTORA 仅供**非盈利内部使用，不对外分发**（仓库根 `INTERNAL_USE.txt`）。本文件是捆绑物卫生清单，不是发布许可声明。

本文件列出**捆绑物**的名称 / 版本 / 许可 / 来源链接 / **GPL 兼容：是/否**（卫生标注，不是分发门禁）。
证据来自磁盘文件（wheel `METADATA` / `LICENSE*`、`ffmpeg -version`、`FONTS_MANIFEST.json`、模型目录）以及 2026-09-16 查阅的官方页面。**没有「应该是 MIT」的猜测。**

配套全文：`resources/THIRD_PARTY_LICENSES.md` 与 `resources/licenses/`（T09 pip-licenses 索引）。本文件是 T10a 的 GPL 兼容性清单。

扫描范围（与 `tests/test_third_party_manifest_complete.py` 一致，**不**遍历 `runtime/venv`）：

- `runtime/ffmpeg/`
- `runtime/models/` 顶层名 + 该树下 `.bin` / `.onnx` / `.exe` / `.dll` / `.pyd` / `.ttf`
- `resources/fonts/` 下 `.ttf`

### 已知平台限制

**Blackwell (sm_120，RTX 50 系，Luca 的 5060 Ti)**：CTranslate2 / faster-whisper 在 GPU 上要 `int8` 会直接抛
`Requested int8 compute type, but the target device or backend do not support efficient int8 computation`。
T12f 加载时把 GPU 档的 int8 改成 `detect_gpu_config` 给出的 **float16**（`cutora/asr/tier.py` `gpu_compute`）。CPU int8 不受影响。

---

## 0. STOP — GPL-incompatible（必须换件或拿掉才能对外按 GPL 分发）

| Name | Version / 路径 | License | Source URL | GPL 兼容 |
|---|---|---|---|---|
| VoxPopuli Italian ASR（`align-it`） | `runtime/models/align_wav2vec2/wav2vec2_voxpopuli_base_10k_asr_it.pt` sha256 `620bad57…` | **CC-BY-NC-4.0**（HF 卡片 YAML `license: cc-by-nc-4.0`；内部非盈利用） | https://huggingface.co/facebook/wav2vec2-base-10k-voxpopuli-ft-it | **否（NC；内部用）** |
| NVIDIA cuDNN | `ctranslate2/cudnn64_9.dll`（随 CTranslate2 4.6.3 wheel） | NVIDIA 专有 EULA | 磁盘：`runtime/venv/Lib/site-packages/ctranslate2/cudnn64_9.dll` | **否（专有；内部包捆绑）** |
| NVIDIA CUDA runtime（PyTorch GPU wheel） | torch **2.7.1+cu128** | NVIDIA CUDA Toolkit EULA（专有） | 磁盘：`runtime/venv/Lib/site-packages/torch-2.7.1+cu128.dist-info/` | **否（专有；内部包捆绑）** |
| Intel OpenMP `libiomp5md.dll` | 随 torch / CTranslate2 wheel | Intel Simplified Software License（专有） | 磁盘：`runtime/venv/Lib/site-packages/` | **否（专有；内部包捆绑）** |
| pyannote segmentation-3.0 | **不捆绑**（`models.json` `bundled: false` `enabled: false` `gated: true` `license: other`；磁盘无权重） | **other** | https://huggingface.co/pyannote/segmentation-3.0 | **否 · 不捆绑** |

**`align-it` 清单曾经误标 Apache-2.0**，实为 CC-BY-NC-4.0。内部非盈利用已恢复 VoxPopuli `.pt`。jonatasgrosman XLSR-53 large italian 作为质量对比候选（`align-it-xlsr`），未测前不启用。

OFL 字体：GNU license-list 把 SIL OFL 1.1 列为 **GPL-incompatible 软件许可证**（保留字体名限制）。CUTORA 把字体当**独立数据文件**捆绑（OFL 明确允许 bundled with software，字体仍保持 OFL、不改成 GPL）。下表字体行标「是（独立字体资产）」；若把字体当代码合并进 GPL 作品则应视为否。详见第 4 节。

Inno Setup：本树尚未捆绑（T10b）。

---

## 1. 运行时二进制 / 库

| Name | Version | License | Source URL | GPL 兼容 |
|---|---|---|---|---|
| FFmpeg gyan **full_build**（`ffmpeg.exe` / `ffprobe.exe`） | `2026-01-07-git-af6a1dd0b2-full_build-www.gyan.dev`；`ffmpeg -version` 含 `--enable-gpl --enable-version3 --enable-libx264 --enable-libx265` | **GPLv3**（gyan：*All builds are 64-bit, static and licensed as GPLv3*） | 磁盘：`runtime/ffmpeg/ffmpeg.exe`；https://www.gyan.dev/ffmpeg/builds/ ；源码对应 FFmpeg git https://github.com/FFmpeg/FFmpeg | **是** |
| PySide6 / Qt for Python | **6.10.2** | 磁盘 `PySide6-6.10.2.dist-info/METADATA`：`LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`。wheel 只带了 `LicenseRef-Qt-Commercial.txt`；CUTORA **动态链接** `.dll`/`.pyd`，走 LGPL 选项 | https://pyside.org ；https://code.qt.io/cgit/pyside/pyside-setup.git/ ；LGPL-3.0 https://www.gnu.org/licenses/lgpl-3.0.html | **是** |
| faster-whisper | **1.2.1** | **MIT**（磁盘 `faster_whisper-1.2.1.dist-info/LICENSE`，Copyright (c) 2023 SYSTRAN） | https://github.com/SYSTRAN/faster-whisper | **是** |
| CTranslate2（代码） | **4.6.3** | **MIT**（磁盘 `ctranslate2-4.6.3.dist-info/METADATA` `License: MIT`；上游 https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE Copyright SYSTRAN / OpenNMT Authors） | https://github.com/OpenNMT/CTranslate2 | **是**（仅代码。同 wheel 内 `cudnn64_9.dll` 见 §0） |
| numpy | **1.26.4** | **BSD-3-Clause**（磁盘 `numpy-1.26.4.dist-info/LICENSE.txt`；Classifier `License :: OSI Approved :: BSD License`） | https://numpy.org ；https://github.com/numpy/numpy | **是** |
| opencv-python（包装） | **4.9.0.80** | 磁盘 `opencv_python-4.9.0.80.dist-info/LICENSE.txt` 为 **MIT**（Copyright Olli-Pekka Heinisuo，opencv-python 包装仓） | https://github.com/opencv/opencv-python | **是** |
| OpenCV 库本体 | 随 opencv-python 4.9.0.80 | **Apache-2.0**（METADATA `License: Apache 2.0`；上游 opencv/opencv LICENSE） | https://github.com/opencv/opencv ；https://github.com/opencv/opencv/blob/4.9.0/LICENSE | **是** |
| Silero VAD | faster-whisper 捆绑 `silero_vad_v6.onnx` | **MIT**（上游 `snakers4/silero-vad` LICENSE：Copyright (c) 2020-present Silero Team；faster-whisper `vad.py` 注明 adapted from that repo） | 磁盘：`runtime/venv/Lib/site-packages/faster_whisper/assets/silero_vad_v6.onnx`；https://github.com/snakers4/silero-vad | **是** |
| CPython（计划 `runtime/python`） | **3.10.11**（本机 venv `pyvenv.cfg`：`home = C:\Program Files\Python310`，`version = 3.10.11`）。当前 checkout **没有** `runtime/python/`；T10b 才拷 embeddable | **PSF License v2** | https://docs.python.org/3.10/license.html ；https://www.python.org/psf/license/ | **是** |
| whisperX（vendor） | vendored `vendor/whisperX`，磁盘 `whisperx` 3.7.6 | **BSD-2-Clause**（`vendor/whisperX/LICENSE` Copyright (c) 2024, Max Bain） | https://github.com/m-bain/whisperX | **是** |
| onnxruntime | **1.23.2** | **MIT**（METADATA `License: MIT License`） | https://onnxruntime.ai ；https://github.com/microsoft/onnxruntime | **是** |
| PyTorch（不含 CUDA 运行库） | **2.7.1+cu128** | **BSD-3-Clause**（磁盘 `torch-2.7.1+cu128.dist-info/LICENSE` + METADATA Classifier BSD） | https://pytorch.org/ ；https://github.com/pytorch/pytorch | **是**（CUDA 二进制见 §0） |
| Intel OpenMP | `ctranslate2/libiomp5md.dll` | Intel Simplified Software License（专有/附加限制，非 GPL） | 随 CTranslate2 4.6.3 Windows wheel | **否** |

---

## 2. `runtime/models` 里实际存在的 ASR / 对齐 / VAD

本目录**没有** `LICENSE` / `README` 文件（已列目录）。许可全部按官方模型卡 / 上游 LICENSE 核对。

| Name | Version / 文件 | License | Source URL | GPL 兼容 |
|---|---|---|---|---|
| asr_small / `model.bin` | Systran/faster-whisper-small revision `536b0662742c02347bc0e980a01041f333bce120`；`model.bin` sha256 `3e305921…` | **MIT**（HF 卡片 `license: mit`；转换自 openai/whisper-small。OpenAI 原仓 LICENSE 亦为 MIT：https://github.com/openai/whisper/blob/main/LICENSE Copyright (c) 2022 OpenAI。HF `openai/whisper-small` 卡片另标 apache-2.0，CUTORA 用的是 Systran CT2 转换，以 Systran 卡片 + OpenAI 原仓为准） | https://huggingface.co/Systran/faster-whisper-small ；https://github.com/openai/whisper | **是** |
| ~~asr_medium~~ | **已从产品删除**（2026-09-16）。五篇英语讲道均值差于 small，体积 1.5 GB。 | MIT | 已从磁盘删除 | **不捆绑** |
| ~~asr_large-v3~~ | **已从产品删除**（2026-09-16）。清晰英语讲道上重复/幻觉；turbo 已赢。 | MIT | 已从磁盘删除 | **不捆绑** |
| asr_large-v3-turbo / `model.bin` | mobiuslabsgmbh/faster-whisper-large-v3-turbo revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`；`model.bin` sha256 `e76620f8…` | **MIT**（HF 卡片 `license: mit`） | https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo | **是** |
| asr_fake | 空测试目录 | n/a（无权重） | 本仓库测试夹具 | **是** |
| align_wav2vec2 / `wav2vec2_fairseq_large_lv60k_asr_ls960.pth` | English wav2vec2 large lv60k ASR / LibriSpeech 960h；`models.json` `align-en` | **Apache-2.0**（Facebook wav2vec2 HF 卡片 `license: apache-2.0`） | 磁盘：`runtime/models/align_wav2vec2/` | **是** |
| ~~align_wav2vec2 / `pytorch_model.bin` 1,269,737,156 B~~ | **已删除**。`config.json` 架构是 `Wav2Vec2ForPreTraining`（未微调预训练权重），sha256 `314340227371a608f71adcd5f0de5933824fe77e55822aa4b24dba9c1c364dcb`。与 align-en 的 `.pth` 不是同一文件。HF 下载残留，清单未认领（BUILD.txt 曾标 unpinned extra）。 | 预训练基座多为 Apache-2.0，但未进清单、未被加载 | 已从磁盘删除 | **不捆绑** |
| align_wav2vec2 / `wav2vec2_voxpopuli_base_10k_asr_it.pt` | `align-it` 现行 | **CC-BY-NC-4.0**（见 §0） | https://huggingface.co/facebook/wav2vec2-base-10k-voxpopuli-ft-it | **否（NC；内部用）** |
| align_it / `pytorch_model.bin` | `align-it-xlsr` jonatasgrosman/wav2vec2-large-xlsr-53-italian revision `dab04a3e…`；质量对比候选，产品默认仍是 VoxPopuli `align-it` | **Apache-2.0**（HF README YAML `license: apache-2.0`） | https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-italian | **是** |
| align_mms / `model.safetensors` | MahmoudAshraf/mms-300m-1130-forced-aligner；加载尺寸不对，对比表先放着 | **CC-BY-NC-4.0**（内部非盈利用） | https://huggingface.co/MahmoudAshraf/mms-300m-1130-forced-aligner | **否（NC；内部用）** |
| align_de / `pytorch_model.bin` | jonatasgrosman/wav2vec2-large-xlsr-53-german revision `4b8a0295…` | **Apache-2.0**（HF README YAML `license: apache-2.0`） | https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-german | **是** |
| align_es / `pytorch_model.bin` | jonatasgrosman/wav2vec2-large-xlsr-53-spanish revision `96d7e9b4…` | **Apache-2.0**（YAML `license: apache-2.0`） | https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-spanish | **是** |
| align_fr / `pytorch_model.bin` | jonatasgrosman/wav2vec2-large-xlsr-53-french revision `7c79e105…` | **Apache-2.0**（YAML `license: apache-2.0`） | https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-french | **是** |
| align_pt / `pytorch_model.bin` | jonatasgrosman/wav2vec2-large-xlsr-53-portuguese revision `634ac655…` | **Apache-2.0**（YAML `license: apache-2.0`） | https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-portuguese | **是** |
| vad_segmentation | **不捆绑** | **other**（见 §0） | https://huggingface.co/pyannote/segmentation-3.0 | **否 · 不捆绑** |
| vad_silero / `silero_vad_v6.onnx` | 不在 `runtime/models/`，在 faster-whisper assets | **MIT** | 见第 1 节 Silero VAD | **是** |
| matte_sam21_large / `model.safetensors` | facebook/sam2.1-hiera-large revision `665f8e2ad61cf5f53d65644ff27c8ee525124610`；`model.safetensors` sha256 `dc407dce…`（+ config.json / preprocessor_config.json / processor_config.json / video_preprocessor_config.json 均钉死）；Q152-D AI 抠像，按需经许可闸门下载，本地推理 | **Apache-2.0**（SAM 2 代码与权重 LICENSE：Apache License 2.0，Meta Platforms） | https://huggingface.co/facebook/sam2.1-hiera-large ；https://github.com/facebookresearch/sam2 | **是** |
| matte_sam21_base_plus / `model.safetensors` | facebook/sam2.1-hiera-base-plus revision `b7320756a13354e7530a63935656d35b2f91a290`；`model.safetensors` sha256 `2012733a…`（配置文件同上钉死）；轻量档 | **Apache-2.0** | https://huggingface.co/facebook/sam2.1-hiera-base-plus | **是** |
| track_tapnextpp_512 / `tapnextpp_512.ckpt` | google/tapnet revision `5d3fb48e76c5422841e38501514121e251beabb7`；sha256 `6cd0e793…`；Q152-E AI 跟踪（TAPNext++ 512，默认），按需经许可闸门下载，本地推理，weights_only 加载 | **Apache-2.0**（HF 卡片：All software and model checkpoints … Apache License 2.0） | https://huggingface.co/google/tapnet | **是** |
| track_tapnextpp_256 / `tapnextpp_ckpt.pt` | 同仓库同 revision；sha256 `cb96a434…`；快速档 | **Apache-2.0** | https://huggingface.co/google/tapnet | **是** |
| `cutora/third_party/tapnext/`（源码收录） | google-deepmind/tapnet 提交 `730cda1c…` 的 TAPNext / TAPNext++ PyTorch 推理代码，去掉 einops / torchvision 依赖（改动处标 `CUTORA:`），LICENSE 原文同目录 | **Apache-2.0** | https://github.com/google-deepmind/tapnet | **是** |

目录 `runtime/models/` 顶层名：`align_de` `align_es` `align_fr` `align_it` `align_mms` `align_pt` `align_wav2vec2` `asr_fake` `asr_large-v3-turbo` `asr_small` `vad_segmentation` `model_paths.json`。

---

## 3. 字体（`resources/fonts`）

`FONTS_MANIFEST.json`：`font_count` **243**（含 `fonts_pending/` 登记项），已注册目录内 **241** 个 `.ttf`。

按许可证（manifest `by_license`）：**OFL-1.1 = 233**，**Apache-2.0 = 10**。隔离 0，缺许可证文件 0。每个家族目录附 `OFL.txt` / `LICENSE.txt` / `LICENSE`。

来源以 Google Fonts 官方仓为主：https://github.com/google/fonts （`ofl/` 或 `apache/`）。Noto 家族见 `resources/fonts/PROVENANCE.json`（commit `809e4d8b8d7e9364a914909bb777679606c178b8`）。

SIL OFL 1.1 文本：http://scripts.sil.org/OFL ；Apache-2.0：http://www.apache.org/licenses/LICENSE-2.0

| Name | Version（manifest） | License | Source URL | GPL 兼容 |
|---|---|---|---|---|
| 已注册 OFL 字体（233 文件，家族见清单） | 见 `FONTS_MANIFEST.json` | **OFL-1.1**（磁盘各目录 `OFL.txt` / LiberationSans `LICENSE` 为 SIL OFL 1.1） | https://github.com/google/fonts ；OFL https://openfontlicense.org/ | **是（独立字体资产）** |
| Arimo | Version from TTF name table | **Apache-2.0**（磁盘 `resources/fonts/Arimo/LICENSE.txt` 为 Apache License 2.0） | https://github.com/google/fonts/tree/main/apache/arimo | **是** |
| Calligraffitti | | **Apache-2.0** | https://github.com/google/fonts/tree/main/apache/calligraffitti | **是** |
| Homemade Apple | | **Apache-2.0** | https://github.com/google/fonts/tree/main/apache/homemadeapple | **是** |
| Permanent Marker | | **Apache-2.0**（磁盘 `Permanent_Marker/LICENSE.txt` 为 Apache 2.0） | https://github.com/google/fonts/tree/main/apache/permanentmarker | **是** |
| Roboto Condensed | | **Apache-2.0** | https://github.com/google/fonts/tree/main/apache/robotocondensed | **是** |
| Roboto Slab | | **Apache-2.0** | https://github.com/google/fonts/tree/main/apache/robotoslab | **是** |
| Rock Salt | | **Apache-2.0** | https://github.com/google/fonts/tree/main/apache/rocksalt | **是** |
| Satisfy | | **Apache-2.0** | https://github.com/google/fonts/tree/main/apache/satisfy | **是** |
| Noto Color Emoji | | **OFL-1.1** | https://github.com/googlefonts/noto-emoji | **是（独立字体资产）** |

Apache-2.0 的 10 个文件 basename：`Arimo-Bold.ttf` `Arimo-Regular.ttf` `Calligraffitti-Regular.ttf` `HomemadeApple-Regular.ttf` `PermanentMarker-Regular.ttf` `RobotoCondensed-Italic-VariableFont_wght.ttf` `RobotoCondensed-VariableFont_wght.ttf` `RobotoSlab-VariableFont_wght.ttf` `RockSalt-Regular.ttf` `Satisfy-Regular.ttf`。

---

## 4. 测试用 basename 清单

以下名称必须出现在本文件中，供 `tests/test_third_party_manifest_complete.py` 对扫描到的 `.dll .pyd .exe .onnx .bin .ttf` **basename** 做子串匹配。由磁盘生成，见下一节。

ffmpeg.exe
ffprobe.exe
model.bin
pytorch_model.bin
silero_vad_v6.onnx
cudnn64_9.dll
wav2vec2_fairseq_large_lv60k_asr_ls960.pth
wav2vec2_voxpopuli_base_10k_asr_it.pt

---

## 5. `.ttf` basename 库存（磁盘 `resources/fonts`）

（由 T10a 生成器追加）

AbrilFatface-Regular.ttf
Acme-Regular.ttf
Akronim-Regular.ttf
Aladin-Regular.ttf
AlfaSlabOne-Regular.ttf
Alkatra-VariableFont_wght.ttf
Allan-Bold.ttf
Allan-Regular.ttf
Allura-Regular.ttf
AlumniSansCollegiateOne-Regular.ttf
AlumniSansInlineOne-Regular.ttf
Anton-Regular.ttf
AntonSC-Regular.ttf
ArchivoBlack-Regular.ttf
Arimo-Bold.ttf
Arimo-Regular.ttf
Arizonia-Regular.ttf
AsapCondensed-Bold.ttf
AsapCondensed-Regular.ttf
AveriaSerifLibre-Bold.ttf
AveriaSerifLibre-Regular.ttf
BadScript-Regular.ttf
Bangers-Regular.ttf
BarlowCondensed-Black.ttf
BarlowCondensed-Bold.ttf
BarlowCondensed-Regular.ttf
BebasNeue-Regular.ttf
BigShouldersStencil-VariableFont_opsz,wght.ttf
BitcountPropSingleInk-VariableFont_CRSV,ELSH,ELXP,SZP1,SZP2,XPN1,XPN2,YPN1,YPN2,slnt,wght.ttf
BlackOpsOne-Regular.ttf
BonheurRoyale-Regular.ttf
Boogaloo-Regular.ttf
BricolageGrotesque-VariableFont_opsz,wdth,wght.ttf
BungeeOutline-Regular.ttf
BungeeShade-Regular.ttf
BungeeSpice-Regular.ttf
BungeeTint-Regular.ttf
CairoPlay-VariableFont_slnt,wght.ttf
Calistoga-Regular.ttf
Calligraffitti-Regular.ttf
Carattere-Regular.ttf
CarterOne-Regular.ttf
Caveat-VariableFont_wght.ttf
ChangaOne-Italic.ttf
ChangaOne-Regular.ttf
Charm-Bold.ttf
Charm-Regular.ttf
Cinzel-VariableFont_wght.ttf
Codystar-Light.ttf
Codystar-Regular.ttf
Comforter-Regular.ttf
ComicRelief-Bold.ttf
ComicRelief-Regular.ttf
Cookie-Regular.ttf
Courgette-Regular.ttf
DancingScript-VariableFont_wght.ttf
Diplomata-Regular.ttf
DiplomataSC-Regular.ttf
DMSans-Italic-VariableFont_opsz,wght.ttf
DMSans-VariableFont_opsz,wght.ttf
DMSerifDisplay-Italic.ttf
DMSerifDisplay-Regular.ttf
DMSerifText-Italic.ttf
DMSerifText-Regular.ttf
EagleLake-Regular.ttf
EduSAHand-VariableFont_wght.ttf
ElsieSwashCaps-Black.ttf
ElsieSwashCaps-Regular.ttf
EmilysCandy-Regular.ttf
FasterOne-Regular.ttf
FiraSans-Black.ttf
FiraSans-Bold.ttf
FiraSans-Regular.ttf
FjallaOne-Regular.ttf
FleurDeLeah-Regular.ttf
FrancoisOne-Regular.ttf
FrederickatheGreat-Regular.ttf
Frijole-Regular.ttf
FugazOne-Regular.ttf
Galada-Regular.ttf
Gelasio-Italic-VariableFont_wght.ttf
Gelasio-VariableFont_wght.ttf
Girassol-Regular.ttf
Gloock-Regular.ttf
GravitasOne-Regular.ttf
GreatVibes-Regular.ttf
Handlee-Regular.ttf
HomemadeApple-Regular.ttf
Honk-Regular-VariableFont_MORF,SHLN.ttf
IndieFlower-Regular.ttf
Itim-Regular.ttf
Jacquard24Charted-Regular.ttf
Jaini-Regular.ttf
Judson-Bold.ttf
Judson-Italic.ttf
Judson-Regular.ttf
Kalam-Bold.ttf
Kalam-Light.ttf
Kalam-Regular.ttf
KalniaGlaze-VariableFont_wdth,wght.ttf
Karantina-Bold.ttf
Karantina-Light.ttf
Karantina-Regular.ttf
Katibeh-Regular.ttf
KleeOne-Regular.ttf
KleeOne-SemiBold.ttf
LeagueGothic-Regular-VariableFont_wdth.ttf
LiberationSans-Bold.ttf
LiberationSans-Regular.ttf
LibreCaslonText-Bold.ttf
LibreCaslonText-Regular.ttf
LilitaOne-Regular.ttf
Lobster-Regular.ttf
LobsterTwo-Bold.ttf
LobsterTwo-Regular.ttf
Lora-Italic-VariableFont_wght.ttf
Lora-VariableFont_wght.ttf
Lugrasimo-Regular.ttf
MadimiOne-Regular.ttf
MarckScript-Regular.ttf
Merienda-VariableFont_wght.ttf
Mogra-Regular.ttf
Mohave-Italic-VariableFont_wght.ttf
Mohave-VariableFont_wght.ttf
Monofett-Regular.ttf
Nabla-Regular-VariableFont_EDPT,EHLT.ttf
Neonderthaw-Regular.ttf
Niconne-Regular.ttf
Nosifer-Regular.ttf
NotoColorEmoji-COLRv1.ttf
NotoNaskhArabicwght.ttf
NotoSansArabicwdth_wght.ttf
NotoSansArmenianwdth_wght.ttf
NotoSansBengaliwdth_wght.ttf
NotoSansDevanagariwdth_wght.ttf
NotoSansEthiopicwdth_wght.ttf
NotoSansGeorgianwdth_wght.ttf
NotoSansGujaratiwdth_wght.ttf
NotoSansGurmukhiwdth_wght.ttf
NotoSansHebrewwdth_wght.ttf
NotoSansJPwght.ttf
NotoSansKannadawdth_wght.ttf
NotoSansKhmerwdth_wght.ttf
NotoSansKRwght.ttf
NotoSansLaowdth_wght.ttf
NotoSansMalayalamwdth_wght.ttf
NotoSansMyanmarwdth_wght.ttf
NotoSansSCwght.ttf
NotoSansSinhalawdth_wght.ttf
NotoSansTamilwdth_wght.ttf
NotoSansTCwght.ttf
NotoSansTeluguwdth_wght.ttf
NotoSansThaiwdth_wght.ttf
NotoSanswdth_wght.ttf
NotoSerif-Italic-VariableFont_wdth,wght.ttf
NotoSerif-VariableFont_wdth,wght.ttf
NotoSerifTibetanwght.ttf
OleoScript-Bold.ttf
OleoScript-Regular.ttf
OleoScriptSwashCaps-Bold.ttf
OleoScriptSwashCaps-Regular.ttf
OoohBaby-Regular.ttf
OpenSans-Bold.ttf
OpenSans-Regular.ttf
Oswald-VariableFont_wght.ttf
PassionOne-Black.ttf
PassionOne-Bold.ttf
PassionOne-Regular.ttf
PermanentMarker-Regular.ttf
PetitFormalScript-Regular.ttf
PinyonScript-Regular.ttf
PirataOne-Regular.ttf
Playball-Regular.ttf
PlayfairDisplay-Italic-VariableFont_wght.ttf
PlayfairDisplay-VariableFont_wght.ttf
PlayfairDisplaySC-Bold.ttf
PlayfairDisplaySC-Regular.ttf
PlaywriteAT-Italic-VariableFont_wght.ttf
PlaywriteAT-VariableFont_wght.ttf
PlaywriteCUGuides-Regular.ttf
PlaywriteINGuides-Regular.ttf
PlaywriteNZBasic-VariableFont_wght.ttf
PoetsenOne-Regular.ttf
ProtestStrike-Regular.ttf
Quintessential-Regular.ttf
RoadRage-Regular.ttf
Roboto-Bold.ttf
Roboto-Regular.ttf
RobotoCondensed-Italic-VariableFont_wght.ttf
RobotoCondensed-VariableFont_wght.ttf
RobotoSlab-VariableFont_wght.ttf
RockSalt-Regular.ttf
Romanesco-Regular.ttf
Rowdies-Bold.ttf
Rowdies-Regular.ttf
RubikDoodleShadow-Regular.ttf
RubikMarkerHatch-Regular.ttf
RubikPuddles-Regular.ttf
RubikScribble-Regular.ttf
RubikVinyl-Regular.ttf
RubikWetPaint-Regular.ttf
RumRaisin-Regular.ttf
Rye-Regular.ttf
Sancreek-Regular.ttf
Satisfy-Regular.ttf
ShadowsIntoLight-Regular.ttf
ShadowsIntoLightTwo-Regular.ttf
Shrikhand-Regular.ttf
SirinStencil-Regular.ttf
SixtyfourConvergence-Regular-VariableFont_BLED,SCAN,XELA,YELA.ttf
SNPro-Italic-VariableFont_wght.ttf
SNPro-VariableFont_wght.ttf
SonsieOne-Regular.ttf
Splash-Regular.ttf
Sriracha-Regular.ttf
Srisakdi-Bold.ttf
Srisakdi-Regular.ttf
Staatliches-Regular.ttf
StardosStencil-Bold.ttf
StardosStencil-Regular.ttf
StoryScript-Regular.ttf
Tapestry-Regular.ttf
Teko-VariableFont_wght.ttf
TextMeOne-Regular.ttf
Tillana-Bold.ttf
Tillana-Regular.ttf
TitanOne-Regular.ttf
TradeWinds-Regular.ttf
TrainOne-Regular.ttf
TsukimiRounded-Bold.ttf
TsukimiRounded-Regular.ttf
VastShadow-Regular.ttf
ViaodaLibre-Regular.ttf
Vidaloka-Regular.ttf
WaitingfortheSunrise-Regular.ttf
WindSong-Medium.ttf
WindSong-Regular.ttf
Workbench-Regular-VariableFont_BLED,SCAN.ttf
YesevaOne-Regular.ttf
Yesteryear-Regular.ttf
YoungSerif-Regular.ttf

## 6. runtime/models 顶层名

align_de
align_es
align_fr
align_pt
align_wav2vec2
asr_fake
asr_medium
asr_small
model_paths.json
vad_segmentation
