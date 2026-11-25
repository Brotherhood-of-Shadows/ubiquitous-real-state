"""Application domain exceptions for Imob PB."""

from __future__ import annotations


class ImobError(Exception):
    """Base exception for all application and domain errors."""

    def __init__(self, message: str, *, code: str = "imob_error") -> None:
        """Initialize the domain error.

        Args:
            message: Human-readable description of the failure.
            code: Machine-stable error identifier.
        """
        super().__init__(message)
        self.message = message
        self.code = code


class ScraperError(ImobError):
    """Base exception for external real-estate website scraping failures."""


class ScraperTransportError(ScraperError):
    """Raised when an HTTP request to a real-estate website fails or times out."""

    def __init__(self, agency_slug: str, url: str, reason: str) -> None:
        """Initialize transport error with agency and URL context.

        Args:
            agency_slug: Unique slug of the target agency.
            url: Requested URL that failed.
            reason: Underlying transport failure summary.
        """
        super().__init__(
            f"Transport failure for '{agency_slug}' at {url}: {reason}",
            code="scraper_transport_error",
        )
        self.agency_slug = agency_slug
        self.url = url


class ScraperParseError(ScraperError):
    """Raised when an external payload has an unexpected schema or markup."""

    def __init__(self, agency_slug: str, reason: str) -> None:
        """Initialize parse error with agency context.

        Args:
            agency_slug: Unique slug of the target agency.
            reason: Description of the malformed payload.
        """
        super().__init__(
            f"Parse failure for '{agency_slug}': {reason}",
            code="scraper_parse_error",
        )
        self.agency_slug = agency_slug


class PersistenceError(ImobError):
    """Raised when a database operation fails at the repository boundary."""

    def __init__(self, message: str) -> None:
        """Initialize persistence failure.

        Args:
            message: Safe diagnostic summary of the database failure.
        """
        super().__init__(message, code="persistence_error")


class EntityNotFoundError(ImobError):
    """Raised when a requested property or agency does not exist."""

    def __init__(self, entity: str, identifier: str | int) -> None:
        """Initialize not-found error.

        Args:
            entity: Name of the domain entity.
            identifier: Primary key or slug that was not found.
        """
        super().__init__(
            f"{entity} '{identifier}' was not found.",
            code="not_found",
        )
        self.entity = entity
        self.identifier = identifier


class DomainValidationError(ImobError):
    """Raised when input parameters violate domain constraints."""

    def __init__(self, message: str) -> None:
        """Initialize validation failure.

        Args:
            message: Explanation of the invalid value or filter combination.
        """
        super().__init__(message, code="validation_error")
