"""FastAPI app wiring everything together.

Mounts demo + autocheck routers, serves config, healthcheck that distinguishes
"process alive" from "config/vault ready". Selects the Vault backend from env:
- PII_VAULT_BACKEND=memory  -> single-process MemoryVault (forbidden with >1 worker)
- PII_VAULT_BACKEND=redis   -> shared RedisVault (required for multiworker)

If RedisVault is enabled and PII_VAULT_KEY is missing, startup fails (readiness
error) rather than silently disabling encryption (R49, section 11.3).
"""
from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.api.autocheck import build_autocheck_router
from app.api.demo import build_demo_router
from app.api.trust_lab import build_trust_lab_router
from app.core.engine import Engine
from app.detectors.registry import DetectorRegistry
from app.observability.logs import SafeLogger
from app.observability.metrics import Metrics
from app.policies.loader import PolicyLoadError, PolicyStore, load_policy_config
from app.providers.stub import StubProvider


class ConfigUpdateRequest(BaseModel):
    consumer: str
    updates: dict = Field(default_factory=dict)
    admin_key: str = ""
from app.trust_lab.faults import FaultInjector
from app.trust_lab.transport import TransportCounter
from app.security.egress import EgressGuard
from app.security.limits import Limits
from app.vault.base import Vault
from app.vault.crypto import AeadCipher, CryptoError
from app.vault.fingerprint import Fingerprinter, derive_fingerprint_key
from app.vault.lifecycle import Lifecycle
from app.vault.memory import MemoryVault
from app.vault.redis import RedisVault

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs")
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "static")
# React production build (frontend/dist). Served at the service root.
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _load_detector_manifest_version(detectors_path: str) -> str:
    """Read detector_manifest_version from detectors.yaml."""
    import yaml
    try:
        with open(detectors_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return str(data.get("detector_manifest_version", "unknown"))
    except Exception:  # noqa: BLE001
        return "unknown"


def _load_aead_key() -> bytes | None:
    """Load the base64 AEAD key from env. Returns None if unset/empty."""
    raw = os.environ.get("PII_VAULT_KEY", "").strip()
    if not raw:
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001
        raise CryptoError("PII_VAULT_KEY is not valid base64") from None


def _check_multiworker_memory() -> None:
    """Fail fast if MemoryVault is combined with >1 worker (R34)."""
    backend = os.environ.get("PII_VAULT_BACKEND", "memory").strip().lower()
    workers = _env_int("PII_WORKERS", 1)
    if backend == "memory" and workers > 1:
        raise RuntimeError(
            "PII_VAULT_BACKEND=memory is single-process only. "
            "Set PII_VAULT_BACKEND=redis (shared Vault) for multiworker, "
            "or set PII_WORKERS=1. No automatic fallback to per-worker memory."
        )


def build_vault() -> tuple[Vault, Fingerprinter, Lifecycle | None]:
    """Build the Vault, Fingerprinter, and (for redis) a Lifecycle from env."""
    backend = os.environ.get("PII_VAULT_BACKEND", "memory").strip().lower()
    max_entries = _env_int("PII_VAULT_MAX_ENTRIES", 100000)
    max_bytes = _env_int("PII_VAULT_MAX_BYTES", 1024 * 1024 * 1024)
    ttl = _env_int("PII_VAULT_TTL_SECONDS", 3600)

    if backend == "redis":
        key = _load_aead_key()
        if key is None:
            raise RuntimeError(
                "PII_VAULT_BACKEND=redis requires PII_VAULT_KEY (base64 AEAD key). "
                "Encryption must not be silently disabled."
            )
        cipher = AeadCipher(key)
        fp_key = derive_fingerprint_key(key)
        url = os.environ.get("PII_VAULT_REDIS_URL", "redis://127.0.0.1:6379/0")
        vault = RedisVault(
            cipher=cipher,
            url=url,
            max_entries=max_entries,
            max_bytes=max_bytes,
            default_ttl_seconds=ttl,
        )
        return vault, Fingerprinter(fp_key), None

    # memory backend
    _check_multiworker_memory()
    vault = MemoryVault(
        max_entries=max_entries,
        max_bytes=max_bytes,
        default_ttl_seconds=ttl,
    )
    key = _load_aead_key()
    fp_key = derive_fingerprint_key(key) if key is not None else os.urandom(32)
    return vault, Fingerprinter(fp_key), None


def create_app(
    consumers_path: str | None = None,
    detectors_path: str | None = None,
    vault: Vault | None = None,
    fingerprinter: Fingerprinter | None = None,
) -> FastAPI:
    consumers_path = consumers_path or os.path.join(_CONFIG_DIR, "consumers.yaml")
    detectors_path = detectors_path or os.path.join(_CONFIG_DIR, "detectors.yaml")

    policy_config = load_policy_config(consumers_path)
    policy_store = PolicyStore(policy_config)

    registry = DetectorRegistry.from_config(detectors_path)
    engine = Engine(registry.detectors)

    if vault is None:
        vault, fp, _ = build_vault()
    else:
        fp = fingerprinter or Fingerprinter(os.urandom(32))

    # Initial retention for a freshly masked record comes from the configured
    # vault TTL (e.g. 3600s), separate from the shorter replay grace (R35).
    retention = _env_int("PII_VAULT_TTL_SECONDS", 3600)
    lifecycle = Lifecycle(
        vault, engine, fp, policy_store.policy_version, retention_seconds=retention
    )

    logger = SafeLogger()
    metrics = Metrics()
    limits = Limits(
        max_body_bytes=_env_int("PII_MAX_BODY_BYTES", 1048576),
        max_in_flight=_env_int("PII_MAX_IN_FLIGHT", 64),
    )
    egress_guard = EgressGuard(engine=engine)
    stub = StubProvider()
    transport = TransportCounter(stub)
    fault_injector = FaultInjector(enabled=os.environ.get("PII_TRUST_LAB_FAULTS", "").strip() == "1")
    detector_manifest_version = _load_detector_manifest_version(detectors_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if isinstance(vault, RedisVault):
            vault.close()

    app = FastAPI(title="AlfaGen PII Gateway", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):
        """Return a safe 422 that never reflects submitted input values (D12).

        FastAPI's default handler echoes the offending value in detail[].input,
        which can leak personal data. We strip the `input` field and keep only
        the location and a generic message.
        """
        safe = []
        for err in exc.errors():
            item = {
                "type": err.get("type", "validation_error"),
                "loc": err.get("loc", []),
                "msg": err.get("msg", "invalid value"),
            }
            safe.append(item)
        return JSONResponse(
            status_code=422,
            content={"detail": safe},
        )

    app.state.vault = vault
    app.state.policy_store = policy_store
    app.state.engine = engine
    app.state.metrics = metrics
    app.state.lifecycle = lifecycle
    app.state.limits = limits

    app.include_router(
        build_demo_router(
            engine, vault, policy_store, logger, metrics, limits, egress_guard, stub, lifecycle
        )
    )
    app.include_router(
        build_autocheck_router(
            engine, vault, policy_store, logger, metrics, lifecycle, limits=limits
        )
    )
    app.include_router(
        build_trust_lab_router(
            engine, policy_store, registry, transport, fault_injector,
            detector_manifest_version=detector_manifest_version,
            build_version="0.1.0",
        )
    )

    @app.get("/health")
    def health() -> JSONResponse:
        # "process alive" is implied by responding. "ready" requires config+vault.
        try:
            policy_store.get_consumer("autocheck")
            vault.size()
            ready = True
        except Exception:  # noqa: BLE001
            ready = False
        return JSONResponse(
            content={"status": "alive", "ready": ready, "policy_version": policy_store.policy_version}
        )

    @app.get("/config")
    def config() -> JSONResponse:
        consumer_info = {}
        for name in policy_store.consumer_names():
            p = policy_store.get_consumer(name)
            if p is None:
                continue
            consumer_info[name] = {
                "enabled": p.enabled,
                "allow_unmask": p.allow_unmask,
                "allow_llm_egress": p.allow_llm_egress,
                "mask_action": p.default_action,
                "detect_types": p.detect_types or "all_required",
                "authentication": p.authentication,
            }
        return JSONResponse(
            content={
                "policy_version": policy_store.policy_version,
                "consumers": policy_store.consumer_names(),
                "consumer_info": consumer_info,
                "detectors": [d.detector_id for d in registry.detectors],
            }
        )

    @app.post("/config/update")
    def config_update(req: ConfigUpdateRequest) -> JSONResponse:
        """Validate and atomically apply a consumer configuration change.

        Protected server-side (requires the config-update key). Invalid changes
        raise a safe error and leave the active configuration working.
        """
        expected = os.environ.get("PII_CONFIG_UPDATE_KEY", "").strip()
        if not expected or req.admin_key != expected:
            raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "config update requires a key"})
        try:
            updated = policy_store.update_consumer(req.consumer, req.updates)
        except PolicyLoadError as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_config", "message": str(exc)}) from exc
        return JSONResponse(
            content={
                "policy_version": policy_store.policy_version,
                "consumer": req.consumer,
                "updated": {
                    "enabled": updated.enabled,
                    "allow_unmask": updated.allow_unmask,
                    "allow_llm_egress": updated.allow_llm_egress,
                    "mask_action": updated.default_action,
                    "detect_types": updated.detect_types or "all_required",
                },
            }
        )

    @app.get("/metrics")
    def metrics_endpoint() -> JSONResponse:
        return JSONResponse(content=metrics.snapshot())

    @app.get("/")
    def index() -> FileResponse:
        # Serve the React production build if present; otherwise fall back to the
        # legacy static page.
        react_index = os.path.join(_FRONTEND_DIR, "index.html")
        if os.path.exists(react_index):
            return FileResponse(react_index)
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))

    # Serve the React build assets (frontend/dist/assets) if present.
    if os.path.isdir(os.path.join(_FRONTEND_DIR, "assets")):
        app.mount("/assets", StaticFiles(directory=os.path.join(_FRONTEND_DIR, "assets")), name="assets")

    # SPA fallback: any non-API GET route returns the React index so refresh and
    # client-side navigation work. API routes are matched first by FastAPI.
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith(("process", "demo", "config", "health", "metrics", "trust-lab", "static")):
            raise HTTPException(status_code=404, detail="not found")
        react_index = os.path.join(_FRONTEND_DIR, "index.html")
        if os.path.exists(react_index):
            return FileResponse(react_index)
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    return app


app = create_app()
