"""Explicit failures for point-in-time resolution."""


class PITResolutionError(RuntimeError):
    """Base class for resolver contract failures."""


class InvalidPITContextError(PITResolutionError):
    """The request mixed PIT modes or supplied an invalid timestamp."""


class UnknownDatasetError(PITResolutionError):
    """No resolver contract exists for the requested dataset."""


class InvalidLogicalKeyError(PITResolutionError):
    """The supplied logical-key fields do not match the dataset contract."""


class SourceNotFoundError(PITResolutionError):
    """The requested or canonical dataset source does not exist."""


class AmbiguousSourceError(PITResolutionError):
    """No explicit source or sole canonical source can be selected."""


class UnsupportedPITModeError(PITResolutionError):
    """The exact dataset/source pair does not support the requested PIT mode."""
