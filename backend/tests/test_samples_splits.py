"""Tests for samples loading + target-disjoint stratified splits."""

import numpy as np

from exovet.data.samples import make_splits


def _fake(n_per_class=25, n_classes=5, seed=0):
    rng = np.random.default_rng(seed)
    y = np.repeat(np.arange(n_classes), n_per_class)
    tid = rng.permutation(np.arange(1000, 1000 + len(y)))
    return y, tid


def test_split_sizes_and_disjointness():
    y, tid = _fake()
    s = make_splits(y, tid, val_frac=0.15, test_frac=0.15)
    tr, va, te = s["train"], s["val"], s["test"]
    assert len(tr) + len(va) + len(te) == len(y)
    assert not set(tr) & set(va)
    assert not set(tr) & set(te)
    assert not set(va) & set(te)


def test_stratification_roughly_balanced():
    y, tid = _fake(n_per_class=30)
    s = make_splits(y, tid, val_frac=0.15, test_frac=0.15)
    counts = np.bincount(y[s["test"]], minlength=5)
    # each class should appear in the test split
    assert (counts > 0).all()


def test_deterministic_given_seed():
    y, tid = _fake()
    a = make_splits(y, tid, seed=5)
    b = make_splits(y, tid, seed=5)
    assert np.array_equal(a["train"], b["train"])
    assert np.array_equal(a["test"], b["test"])
