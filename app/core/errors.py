"""
Domain exception hierarchy and its HTTP projection.

Services raise these; a single exception handler in `app.main` turns them into
RFC-7807-shaped responses. Services never raise `HTTPException`, so they stay
transport-agnostic and unit-testable, and internal provider strings never leak
verbatim to a caller — each error carries a separate `public_message`.
"""

from __future__ import annotations

from http import HTTPStatus


class IVRError(Exception):
    """Base class for every expected failure in the engine."""

    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    public_message: str = "Something went wrong on our side. Please try again."

    def __init__(self, detail: str | None = None, *, public_message: str | None = None) -> None:
        super().__init__(detail or self.public_message)
        self.detail = detail or self.public_message
        if public_message is not None:
            self.public_message = public_message


# ------------------------------------------------------------------------------
# Upstream provider failures (Sarvam AI)
# ------------------------------------------------------------------------------
class ProviderError(IVRError):
    status_code = HTTPStatus.BAD_GATEWAY
    code = "provider_error"
    public_message = "We're having trouble reaching support systems right now."


class ProviderCreditExhaustedError(ProviderError):
    status_code = HTTPStatus.PAYMENT_REQUIRED
    code = "provider_credit_exhausted"
    public_message = "This line is temporarily unavailable. Please try again later."


class ProviderRateLimitedError(ProviderError):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "provider_rate_limited"
    public_message = "We're handling a lot of calls right now. Please try again in a moment."


class ProviderTimeoutError(ProviderError):
    status_code = HTTPStatus.GATEWAY_TIMEOUT
    code = "provider_timeout"
    public_message = "That took longer than expected. Could you repeat that?"


# ------------------------------------------------------------------------------
# Caller / request failures
# ------------------------------------------------------------------------------
class BadRequestError(IVRError):
    status_code = HTTPStatus.BAD_REQUEST
    code = "bad_request"
    public_message = "That request could not be processed."


class EmptyAudioError(BadRequestError):
    code = "empty_audio"
    public_message = "The uploaded audio file is empty."


class AudioTooLargeError(BadRequestError):
    status_code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    code = "audio_too_large"
    public_message = "That audio clip is too large. Keep each turn under a few megabytes."


class NoSpeechDetectedError(IVRError):
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "no_speech_detected"
    public_message = "I couldn't hear anything in that. Could you try again?"


class CustomerNotFoundError(IVRError):
    status_code = HTTPStatus.NOT_FOUND
    code = "customer_not_found"
    public_message = "No subscriber found for that number."


class InvalidFieldUpdateError(BadRequestError):
    code = "invalid_field_update"
    public_message = "That field cannot be updated on a subscriber record."


# ------------------------------------------------------------------------------
# Access control / throttling
# ------------------------------------------------------------------------------
class UnauthorizedError(IVRError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "unauthorized"
    public_message = "A valid operator API key is required for this endpoint."


class RateLimitedError(IVRError):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "rate_limited"
    public_message = "Too many turns too quickly. Please slow down."


class ServiceUnavailableError(IVRError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "service_unavailable"
    public_message = "The engine is not ready to take calls yet."
