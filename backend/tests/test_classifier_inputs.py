"""Catalog-selection masking and the out-of-distribution flag."""

import numpy as np
import pytest

from exovet.config import settings
from exovet.training.prepare import fit_scaler, out_of_distribution, transform_scalars

NAMES = ["snr", "Tmag", "dist_pc", "depth_ppm_log10"]


@pytest.fixture
def masked(monkeypatch):
    monkeypatch.setattr(settings, "CLASSIFIER_EXCLUDED_SCALARS", ("Tmag", "dist_pc"))


def _train():
    rng = np.random.default_rng(0)
    x = np.column_stack([rng.normal(15, 3, 200), rng.normal(11, 1.5, 200),
                         rng.normal(400, 150, 200), rng.normal(3.5, 0.4, 200)])
    return fit_scaler(x, NAMES)


def test_excluded_scalars_are_masked_to_train_mean(masked):
    sc = _train()
    assert sc["excluded_names"] == ["Tmag", "dist_pc"]
    # Pi-Men-like: extremely bright and near — must not reach the classifier
    z = transform_scalars(np.array([[33.0, 5.1, 18.3, 2.48]], dtype=np.float32), sc)
    assert z[0, 1] == 0.0 and z[0, 2] == 0.0
    assert z[0, 0] != 0.0 and z[0, 3] != 0.0


def test_ood_flags_unseen_inputs_but_not_masked_ones(masked):
    sc = _train()
    vec = np.array([60.0, 5.1, 18.3, 3.5])          # snr far outside training
    ood = out_of_distribution(vec, sc, NAMES)
    assert ood["checked"] and ood["flag"]
    assert [f["name"] for f in ood["features"]] == ["snr"]


def test_ood_quiet_for_typical_target_and_nan():
    sc = _train()
    ood = out_of_distribution(np.array([15.0, 11.0, np.nan, 3.4]), sc, NAMES)
    assert ood["checked"] and not ood["flag"] and ood["features"] == []


def test_legacy_scaler_reports_unchecked_not_all_clear():
    legacy = {"median": [0.0], "mean": [0.0], "std": [1.0]}
    ood = out_of_distribution(np.array([99.0]), legacy, ["snr"])
    assert ood["checked"] is False and ood["flag"] is False


def test_unmasked_model_flags_pi_men_brightness():
    # served configuration: nothing masked, so Pi Men's Tmag is checked
    sc = _train()
    assert sc["excluded"] == []
    ood = out_of_distribution(np.array([15.0, 5.1, 18.3, 3.5]), sc, NAMES)
    assert ood["flag"] and "Tmag" in [f["name"] for f in ood["features"]]
