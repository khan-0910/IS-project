"""MC-Dropout tests: run twice, expect identical statistics with fixed input.

A randomly initialised network is fine here — we are testing the sampling
machinery (mode switching, aggregation, flag logic), not accuracy.
"""

import numpy as np
import torch

from exovet.models.transformer import ExoVetTransformer
from exovet.uncertainty.mc_dropout import mc_predict


def _model():
    torch.manual_seed(0)
    return ExoVetTransformer(n_scalars=33)


def _inputs():
    rng = np.random.default_rng(0)
    return {
        "global_v": rng.normal(0, 0.01, 2048).astype(np.float32),
        "local_v": rng.normal(0, 0.01, 256).astype(np.float32),
        "phase_v": rng.normal(0, 0.01, 256).astype(np.float32),
        "scalars": rng.normal(0, 1, 33).astype(np.float32),
    }


def test_mc_predict_shape_and_flags():
    model = _model()
    out = mc_predict(model, n_passes=8, **_inputs())
    assert len(out["mean_probs"]) == 5
    assert abs(sum(out["mean_probs"]) - 1.0) < 1e-5
    assert out["flags"]["verdict"] in ("confident", "ambiguous", "unstable")
    assert out["predicted_class"] in out["class_names"]


def test_mc_predict_deterministic_given_seed():
    torch.manual_seed(42)
    model = _model()
    a = mc_predict(model, n_passes=8, **_inputs())
    torch.manual_seed(42)
    model2 = _model()
    b = mc_predict(model2, n_passes=8, **_inputs())
    assert a["mean_probs"] == pytest.approx(b["mean_probs"], abs=1e-6)


import pytest  # noqa: E402
