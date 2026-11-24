"""Secure secret retrieval backed by Google Cloud Secret Manager.

Ensures zero API keys or database credentials are hardcoded anywhere in the
codebase. In production on Google Cloud, secrets are retrieved via the Secret
Manager API (or mounted by Cloud Run from Secret Manager); locally, secrets are
read exclusively from process environment variables.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.observability.logging import get_structured_logger, log_structured_event

_logger = get_structured_logger(__name__)

_PLACEHOLDER_PREFIXES = ("your-", "replace-with-", "example-", "changeme")


class SecretResolutionError(RuntimeError):
    """Raised when a required secret cannot be resolved securely."""


class SecretManagerClient:
    """Retrieves secrets from Google Cloud Secret Manager with env fallback."""

    def __init__(
        self,
        *,
        project_id: str | None = None,
        use_secret_manager: bool = False,
        gcp_client: Any | None = None,
    ) -> None:
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.use_secret_manager = use_secret_manager and bool(self.project_id)
        self._gcp_client = gcp_client
        self._cache: dict[str, str] = {}

    def _get_gcp_client(self) -> Any:
        if self._gcp_client is None and self.use_secret_manager:
            from google.cloud import secretmanager  # type: ignore[import-untyped]

            self._gcp_client = secretmanager.SecretManagerServiceClient()
        return self._gcp_client

    @staticmethod
    def _is_placeholder(value: str) -> bool:
        normalized = value.strip().lower()
        return not normalized or normalized.startswith(_PLACEHOLDER_PREFIXES)

    def get_secret(
        self,
        secret_id: str,
        *,
        env_var: str | None = None,
        version: str = "latest",
        required: bool = False,
    ) -> str | None:
        """Resolves a secret value by name without ever logging its plaintext.

        Resolution order:
        1. In-process cache
        2. Environment variable (which Cloud Run mounts directly from Secret Manager)
        3. Google Cloud Secret Manager API (when ``use_secret_manager=True``)

        Args:
            secret_id: Secret Manager secret identifier (e.g., ``google-api-key``).
            env_var: Optional environment variable name to check first.
            version: Secret Manager version label (default: ``latest``).
            required: If True, raises ``SecretResolutionError`` when missing.

        Returns:
            The resolved secret string, or ``None`` if optional and unavailable.
        """
        cache_key = f"{secret_id}:{version}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        env_name = env_var or secret_id.upper().replace("-", "_")
        env_value = os.environ.get(env_name)
        if env_value and not self._is_placeholder(env_value):
            self._cache[cache_key] = env_value
            return env_value

        if self.use_secret_manager and self.project_id:
            try:
                client = self._get_gcp_client()
                resource_name = (
                    f"projects/{self.project_id}/secrets/{secret_id}/versions/{version}"
                )
                response = client.access_secret_version(request={"name": resource_name})
                decoded = response.payload.data.decode("utf-8").strip()
                if decoded and not self._is_placeholder(decoded):
                    self._cache[cache_key] = decoded
                    log_structured_event(
                        _logger,
                        logging.INFO,
                        "secret_loaded",
                        "Loaded secret from Google Cloud Secret Manager.",
                        payload={"secret_id": secret_id, "source": "secret_manager"},
                    )
                    return decoded
            except Exception as exc:
                log_structured_event(
                    _logger,
                    logging.WARNING,
                    "secret_load_failed",
                    "Failed to fetch secret from Secret Manager.",
                    payload={"secret_id": secret_id, "error_type": type(exc).__name__},
                )
                if required:
                    raise SecretResolutionError(
                        f"Unable to fetch required secret '{secret_id}' from Secret Manager."
                    ) from exc

        if required:
            raise SecretResolutionError(
                f"Required secret '{secret_id}' (env var '{env_name}') is not configured. "
                "Store it in Google Cloud Secret Manager or set it in your local .env file."
            )
        return None
