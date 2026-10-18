# Copyright (c) Soumith Chintala 2016, All rights reserved. (torchvision, BSD-3-Clause)
# CUTORA 按 torchvision 0.22 的 vision_transformer.EncoderBlock / MLPBlock 重写的同结构实现：
# 参数名（ln_1 / self_attention / ln_2 / mlp.0 / mlp.3）和计算顺序完全一致，所以 TAPNext 的权重能直接 load。
# 这样运行时不需要装 torchvision（它带 C 扩展，必须和 torch 版本严格配对）。
"""Transformer encoder block (same parameter layout as torchvision's EncoderBlock)."""
from __future__ import annotations

from functools import partial

import torch
from torch import nn


class MLPBlock(nn.Sequential):
    """Linear → GELU → Dropout → Linear → Dropout（下标 0 / 3 是两层 Linear，和 torchvision 一致）。"""

    def __init__(self, in_dim: int, mlp_dim: int, dropout: float):
        super().__init__(
            nn.Linear(in_dim, mlp_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(mlp_dim, in_dim),
            nn.Dropout(dropout),
        )

    def _load_from_state_dict(self, state_dict, prefix, local_metadata, strict, missing_keys,
                              unexpected_keys, error_msgs):
        # 和 torchvision 一样兼容老权重的命名（linear_1 / linear_2 → 0 / 3）
        for i in range(2):
            for kind in ("weight", "bias"):
                old_key = "%slinear_%d.%s" % (prefix, i + 1, kind)
                if old_key in state_dict:
                    state_dict["%s%d.%s" % (prefix, 3 * i, kind)] = state_dict.pop(old_key)
        super()._load_from_state_dict(state_dict, prefix, local_metadata, strict, missing_keys,
                                      unexpected_keys, error_msgs)


class EncoderBlock(nn.Module):
    """Pre-norm 自注意力 + MLP，两处残差。"""

    def __init__(self, num_heads: int, hidden_dim: int, mlp_dim: int, dropout: float,
                 attention_dropout: float, norm_layer=partial(nn.LayerNorm, eps=1e-6)):
        super().__init__()
        self.num_heads = num_heads
        self.ln_1 = norm_layer(hidden_dim)
        self.self_attention = nn.MultiheadAttention(hidden_dim, num_heads, dropout=attention_dropout,
                                                    batch_first=True)
        self.dropout = nn.Dropout(dropout)
        self.ln_2 = norm_layer(hidden_dim)
        self.mlp = MLPBlock(hidden_dim, mlp_dim, dropout)

    def forward(self, input: torch.Tensor):  # noqa: A002 - 和上游同名
        x = self.ln_1(input)
        x, _ = self.self_attention(x, x, x, need_weights=False)
        x = self.dropout(x)
        x = x + input
        y = self.ln_2(x)
        y = self.mlp(y)
        return x + y
