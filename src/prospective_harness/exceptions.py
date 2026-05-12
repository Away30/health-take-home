"""Domain exceptions for the prospective harness."""


class ProspectiveHarnessError(Exception):
    """Base class for domain-specific harness errors."""


class ImmutablePredictionError(ProspectiveHarnessError):
    """Raised when a caller attempts to mutate append-only prediction data."""


class TemporalOrderingError(ProspectiveHarnessError):
    """Raised when an outcome was not observed in the permitted prospective window."""


class PredictionNotFoundError(ProspectiveHarnessError):
    """Raised when an operation references an unknown prediction id."""


class DataIntegrityError(ProspectiveHarnessError):
    """Raised when stored prediction data no longer matches its content hash."""
