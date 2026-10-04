"""Error taxonomy for EXOVET.

Every failure the pipeline can produce is an :class:`ExoVetError` carrying a
stable machine code and a human-readable explanation that is safe to show in
the UI.  Unexpected exceptions are mapped to a generic message at the API
layer; tracebacks are logged server-side only.
"""

from __future__ import annotations


class ExoVetError(Exception):
    """Base class for errors with a user-facing message."""

    code = "internal_error"
    http_status = 500

    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.detail:
            out["detail"] = self.detail
        return out


class InvalidInputError(ExoVetError):
    code = "invalid_input"
    http_status = 400


class DataNotFoundError(ExoVetError):
    code = "no_tess_data"
    http_status = 404


class DownloadError(ExoVetError):
    code = "download_failed"
    http_status = 502


class InsufficientDataError(ExoVetError):
    code = "insufficient_data"
    http_status = 422


class UploadUnsupportedError(ExoVetError):
    code = "upload_unsupported"
    http_status = 415


class PreprocessingError(ExoVetError):
    code = "preprocessing_failed"
    http_status = 500


class DetrendError(ExoVetError):
    code = "detrend_failed"
    http_status = 500


class TLSFailureError(ExoVetError):
    code = "tls_failed"
    http_status = 500


class FeatureError(ExoVetError):
    code = "feature_failed"
    http_status = 500


class ModelNotTrainedError(ExoVetError):
    code = "model_not_trained"
    http_status = 503


class InferenceError(ExoVetError):
    code = "model_inference_failed"
    http_status = 500


class TransitFitError(ExoVetError):
    code = "transit_fit_failed"
    http_status = 500
