# -*- coding: utf-8 -*-
"""TAPNext / TAPNext++ 点跟踪（Google DeepMind，Apache License 2.0）的 PyTorch 推理代码，原样收录 + 少量改动。

来源：https://github.com/google-deepmind/tapnet  提交 730cda1c730877cfedbe01bf87fb1cadb78a565d（2026-09-15）
      tapnet/tapnext/{pscan.py, tapnext_lru_modules.py, tapnext_torch.py}
      tapnet/tapnextpp/votsp2026/{model.py, utils.py} → tapnextpp.py
改动（见各文件里的 ``CUTORA:`` 注释，许可证全文见同目录 LICENSE）：
  * 不依赖 einops：rearrange 改成等价的 permute / reshape；
  * 不依赖 torchvision：EncoderBlock 换成 vit_block.py 里参数布局相同的实现；
  * 去掉 torch.export / torch.fx._pytree 注册；
  * 权重加载强制 weights_only=True（运行时全局设了 TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD，这里临时摘掉）；
  * 包名改成 cutora.third_party.tapnext。
权重不在仓库里：从 huggingface.co/google/tapnet 按登记表钉死的 sha256 下载。
"""
