"""Batch verification — compare pipeline verdicts against catalog truth.

Everything in this package follows the project honesty rules:

* ground truth comes ONLY from the ExoFOP TOI table (via
  ``exovet.data.catalogs`` mapping rules) — targets without a published
  disposition are reported as predictions, never scored;
* strict vs relaxed accuracy are both reported (grazing-EB is a geometry
  rule applied on top of the EB label, so predicted-GEB vs truth-EB is a
  rule-subtype split, not a plain error — it is counted both ways, with
  the rule text shipped alongside);
* every mismatch carries machine-readable reason codes naming the exact
  threshold or rule that fired.
"""
