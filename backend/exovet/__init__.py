"""EXOVET — AI-enabled exoplanet vetting from noisy stellar light curves.

Scientific pipeline package. Web presentation lives in /frontend;
this package contains no UI logic.
"""

__version__ = "0.1.0"

# Bumped by scripts/train_transformer.py checkpoints; /api/model reads this
# plus the checkpoint payload (trained_at, test macro-F1, dataset size).
MODEL_VERSION = "transformer-245-v1"
