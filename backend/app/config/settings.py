"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Cyber Agent Platform"
    app_version: str = "1.0.2-rc1"
    app_environment: str = "development"
    api_prefix: str = ""
    api_docs_enabled: bool = True
    debug: bool = False
    database_url: str = "postgresql+asyncpg://cap:cap@postgres:5432/cap"
    redis_url: str = "redis://redis:6379/0"
    secret_key: str = Field(default="change-me", min_length=8)
    jwt_secret: str = Field(default="change-me-too", min_length=8)
    log_level: str = "INFO"
    config_directory: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2] / "config"
    )
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]
    rbac_identity_header: str = "X-CAP-User"
    rbac_trusted_proxy_header: str = "X-CAP-Proxy-Secret"
    rbac_trusted_proxy_secret: str = "change-me-proxy-secret"
    metrics_enabled: bool = True
    tracing_enabled: bool = True
    otel_service_name: str = "cyber-agent-platform"
    otel_exporter_endpoint: str | None = None

    # -- Phase 28.4: production isolation / durable object storage ----------
    # Phase 28.5: sandbox_provider accepts "oci-sandbox" (container runtime)
    # Phase 28.6: accepts "kubernetes-sandbox" (sandbox Pod in cap-sandbox ns;
    # the worker never mounts a container runtime socket)
    sandbox_provider: str = (
        # memory-sandbox | subprocess-sandbox | oci-sandbox | kubernetes-sandbox
        "subprocess-sandbox"
    )
    sandbox_timeout_seconds: int = 120
    sandbox_memory_mb: int = 512
    sandbox_max_processes: int = 64
    # -- Phase 28.5: OCI container sandbox ---------------------------------
    sandbox_image: str = "cap-sandbox-http:latest"
    sandbox_browser_image: str = "cap-sandbox-browser:latest"
    sandbox_network: str = ""
    sandbox_cpu_millicores: int = 500
    sandbox_pids_limit: int = 256
    egress_proxy_url: str = ""
    egress_proxy_port: int = 8080
    egress_allow: str = ""  # test hook: "host:port" allowlist for local lab
    sandbox_reaper_interval_seconds: int = 60
    sandbox_runtime_driver: str = "docker"  # docker | podman | containerd
    # -- Phase 28.6: Kubernetes sandbox ------------------------------------
    sandbox_namespace: str = "cap-sandbox"
    sandbox_shim_port: int = 8080
    sandbox_pod_ready_timeout_seconds: int = 90
    object_store_backend: str = "local"  # local | s3
    object_store_endpoint: str = ""
    object_store_access_key: str = ""
    object_store_secret_key: str = ""
    object_store_bucket: str = "cap-evidence"
    object_store_secure: bool = False
    object_store_max_object_bytes: int = 20 * 1024 * 1024
    orphan_grace_seconds: float = 3600.0
    gc_interval_seconds: int = 3600

    @model_validator(mode="after")
    def reject_insecure_production_defaults(self) -> "Settings":
        """Fail startup when production is configured with repository placeholders."""

        if self.app_environment.casefold() != "production":
            return self
        placeholders = {
            "secret_key": {"change-me", "development-secret"},
            "jwt_secret": {"change-me-too", "development-jwt-secret"},
            "rbac_trusted_proxy_secret": {
                "change-me-proxy-secret",
                "replace-with-a-long-random-proxy-secret",
            },
        }
        insecure = [name for name, values in placeholders.items() if getattr(self, name) in values]
        if insecure:
            raise ValueError(
                "Production configuration contains insecure placeholders: "
                + ", ".join(sorted(insecure))
            )
        if self.debug:
            raise ValueError("DEBUG must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance."""

    return Settings()
