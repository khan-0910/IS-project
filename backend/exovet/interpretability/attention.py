"""Phase 9 — attention maps for the EXOVET Transformer.

Method: attention rollout (Abnar & Zuidema 2020) — through every encoder
layer we propagate the CLS token's attention with the residual-aware
transformation A' = ½·I + A (row-renormalised).  The final CLS row gives the
composed influence of every input token on the prediction.

Token layout (see exovet.models.transformer): CLS + 64 global tokens + 16
local tokens + 16 phase tokens.  Token index → axis coordinate mapping:

* global  — token i covers segment [i/64, (i+1)/64] of the analysed window
* local   — token i covers segment [i/16, (i+1)/16] of the transit window
* phase   — token i covers phase bin [i/16, (i+1)/16] of the orbit

The report returns per-token influence plus per-view totals.  Attention is a
plausibility diagnostic, not a causal explanation — the report labels it as
such and always ships it next to the input data it refers to.
"""

from __future__ import annotations

import numpy as np
import torch

from exovet.models.transformer import VIEW_SPEC


def _token_slices() -> list[tuple[str, int, int]]:
    """(view, start, stop) index ranges inside the token sequence (CLS first)."""
    out, pos = [], 1
    for name in ("global", "local", "phase"):
        n = VIEW_SPEC[name][0] // VIEW_SPEC[name][1]
        out.append((name, pos, pos + n))
        pos += n
    return out


def attention_report(model, global_v, local_v, phase_v, scalars,
                     scaler: dict | None = None) -> dict:
    """One forward pass → token influence + per-view summaries (JSON-ready)."""
    from exovet.training.prepare import (normalize_views,  # noqa: E402
                                         transform_scalars)

    g = torch.from_numpy(normalize_views(np.asarray(global_v)[None, :]))
    l = torch.from_numpy(normalize_views(np.asarray(local_v)[None, :]))
    p = torch.from_numpy(normalize_views(np.asarray(phase_v)[None, :]))
    s = np.asarray(scalars, dtype=np.float32)[None, :]
    sv = (torch.from_numpy(transform_scalars(s, scaler))
          if scaler is not None else torch.from_numpy(s))

    maps = model.attention_maps(g, l, p, sv)
    rollout = torch.eye(maps[0].shape[-1])
    with torch.no_grad():
        for a in maps:
            a = a[0].float().cpu()               # (T, T)
            a = 0.5 * torch.eye(a.shape[-1]) + a
            a = a / a.sum(dim=-1, keepdim=True)
            rollout = a @ rollout

    cls_row = rollout[0].numpy()                 # CLS row (index 0 = self)

    tokens, views = [], {}
    for name, start, stop in _token_slices():
        n = stop - start
        w = cls_row[start:stop]
        share = float(w.sum())
        centers = (np.arange(n) + 0.5) / n
        views[name] = {
            "token_influence": w.tolist(),
            "view_share": share,
            "axis": centers.tolist(),
        }
        for i in range(n):
            tokens.append({"view": name, "index": i,
                           "influence": float(w[i])})

    order = np.argsort(cls_row)[::-1]
    top_tokens = [{"view": tokens[i]["view"], "index": tokens[i]["index"],
                   "influence": tokens[i]["influence"]}
                  for i in order[:10]]

    return {
        "method": "attention rollout (Abnar & Zuidema 2020): composed "
                  "CLS-token attention through all encoder layers",
        "note": "attention highlights where the model looked; it is a "
                "diagnostic, not proof of causation",
        "n_tokens": len(tokens),
        "view_shares": {k: round(v["view_share"], 4) for k, v in views.items()},
        "views": views,
        "top_tokens": top_tokens,
    }
