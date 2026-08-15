"""Errors Core raises on purpose. Each client maps them to its own vocabulary — HTTP status, exit code."""


class DefossilError(Exception):
    """Base of everything Core raises deliberately, so a client can catch the whole family."""


class NotFoundError(DefossilError):
    """A record was asked for by id and does not exist."""


class InvalidOperationError(DefossilError):
    """The operation a caller asked for would break a rule of the model."""


class AiError(DefossilError):
    """A backend call failed or its answer could not be read; the same ask can be retried later."""
