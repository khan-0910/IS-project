"""EXOVET 5-class Transformer (§8 of the spec).

Inputs
------
* global view  (B, 2048)  → patchify (patch 32) → 64 tokens
* local view   (B, 256)   → patchify (patch 16) → 16 tokens
* phase view   (B, 256)   → patchify (patch 16) → 16 tokens
* scalar vector (B, 33)   → small MLP branch

All tokens share one Transformer encoder (d_model 128, 4 heads, 4 layers,
post-LN) with a learnable CLS token.  The CLS output is concatenated with the
scalar branch and classified by a linear head into 5 classes.

Dropout (0.2) is applied inside encoder blocks, the scalar MLP and the head —
at inference we keep those modules in train mode for MC-Dropout uncertainty
(see exovet.uncertainty.mc_dropout).

Attention: the custom encoder layer stores the (head-averaged) attention
matrix of every forward pass in ``layer.last_attn`` so interpretability code
can retrieve per-layer maps without re-running the model.
"""

from __future__ import annotations

import torch
import torch.nn as nn

from exovet.config import settings

# view → (input length, patch length)
VIEW_SPEC = {
    "global": (settings.GLOBAL_POINTS, 32),      # 64 tokens
    "local": (settings.LOCAL_WINDOW_POINTS, 16),  # 16 tokens
    "phase": (settings.PHASE_POINTS, 16),        # 16 tokens
}


class AttnEncoderLayer(nn.Module):
    """Post-LN Transformer layer that records its attention weights."""

    def __init__(self, d_model: int, nhead: int, ff: int, dropout: float):
        super().__init__()
        self.attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout,
                                          batch_first=True)
        self.ln1 = nn.LayerNorm(d_model)
        self.ff = nn.Sequential(
            nn.Linear(d_model, ff), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(ff, d_model), nn.Dropout(dropout),
        )
        self.ln2 = nn.LayerNorm(d_model)
        self.drop = nn.Dropout(dropout)
        self.last_attn: torch.Tensor | None = None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        a, w = self.attn(x, x, x, need_weights=True,
                         average_attn_weights=True)
        self.last_attn = w.detach()
        x = self.ln1(x + self.drop(a))
        x = self.ln2(x + self.ff(x))
        return x


class PatchEmbed(nn.Module):
    """Non-overlapping strided-conv patchifier + per-token projection."""

    def __init__(self, n_in: int, patch: int, d_model: int, dropout: float):
        super().__init__()
        self.proj = nn.Conv1d(1, d_model, kernel_size=patch, stride=patch)
        n_tokens = n_in // patch
        self.pos = nn.Parameter(torch.zeros(1, n_tokens, d_model))
        nn.init.trunc_normal_(self.pos, std=0.02)
        self.drop = nn.Dropout(dropout)
        self.n_tokens = n_tokens

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.proj(x.unsqueeze(1)).transpose(1, 2)   # (B, T, D)
        return self.drop(z + self.pos)


class ExoVetTransformer(nn.Module):
    """Global+local+phase views and scalar features → 5-class logits."""

    def __init__(self, n_scalars: int, d_model: int = 128, nhead: int = 4,
                 n_layers: int = 4, ff: int = 256, dropout: float = 0.2,
                 n_classes: int = 5):
        super().__init__()
        self.embeds = nn.ModuleDict({
            name: PatchEmbed(n_in, patch, d_model, dropout)
            for name, (n_in, patch) in VIEW_SPEC.items()
        })
        # view-type embedding added per token so shared layers know the source
        self.view_emb = nn.Parameter(torch.zeros(3, 1, 1, d_model))
        nn.init.trunc_normal_(self.view_emb, std=0.02)

        self.cls = nn.Parameter(torch.zeros(1, 1, d_model))
        nn.init.trunc_normal_(self.cls, std=0.02)
        self.layers = nn.ModuleList(
            AttnEncoderLayer(d_model, nhead, ff, dropout)
            for _ in range(n_layers)
        )
        self.ln = nn.LayerNorm(d_model)

        self.scalar_branch = nn.Sequential(
            nn.Linear(n_scalars, 64), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(64, 64), nn.GELU(), nn.Dropout(dropout),
        )
        self.head = nn.Linear(d_model + 64, n_classes)

        self.config = {
            "n_scalars": n_scalars, "d_model": d_model, "nhead": nhead,
            "n_layers": n_layers, "ff": ff, "dropout": dropout,
            "n_classes": n_classes,
            "view_spec": {k: list(v) for k, v in VIEW_SPEC.items()},
        }

    def forward(self, global_v: torch.Tensor, local_v: torch.Tensor,
                phase_v: torch.Tensor, scalars: torch.Tensor) -> torch.Tensor:
        views = {"global": global_v, "local": local_v, "phase": phase_v}
        seqs = []
        for i, name in enumerate(("global", "local", "phase")):
            z = self.embeds[name](views[name])          # (B, T_i, D)
            seqs.append(z + self.view_emb[i])
        x = torch.cat(seqs, dim=1)                       # (B, 96, D)
        cls = self.cls.expand(x.shape[0], -1, -1)
        x = torch.cat([cls, x], dim=1)                   # (B, 97, D)
        for layer in self.layers:
            x = layer(x)
        x = self.ln(x[:, 0])                             # CLS token
        s = self.scalar_branch(scalars)
        return self.head(torch.cat([x, s], dim=1))

    @torch.no_grad()
    def attention_maps(self, global_v, local_v, phase_v, scalars):
        """Run one forward pass, return per-layer (B, T, T) attention."""
        was_training = self.training
        self.eval()
        for layer in self.layers:
            layer.last_attn = None
        self.forward(global_v, local_v, phase_v, scalars)
        maps = [layer.last_attn for layer in self.layers]
        if was_training:
            self.train()
        return maps
