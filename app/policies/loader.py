"""Load and validate consumer policy config."""
from __future__ import annotations

import yaml

from app.policies.schema import ConsumerPolicy, PolicyConfig


class PolicyLoadError(Exception):
    """Raised when policy config is invalid."""


def load_policy_config(path: str) -> PolicyConfig:
    """Load configs/consumers.yaml and validate it."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as exc:
        raise PolicyLoadError(f"cannot read policy config: {exc}") from exc
    try:
        config = PolicyConfig.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise PolicyLoadError(f"invalid policy config: {exc}") from exc
    # Derive consumer name from the dict key and validate detect_types.
    for name, consumer in config.consumers.items():
        consumer.name = name
        try:
            consumer.detect_types_set()
        except ValueError as exc:
            raise PolicyLoadError(
                f"consumer '{name}': {exc}"
            ) from exc
    return config


class PolicyStore:
    """Exposes per-consumer settings and supports validated atomic updates."""

    def __init__(self, config: PolicyConfig) -> None:
        self._config = config

    @property
    def policy_version(self) -> str:
        return self._config.policy_version

    def get_consumer(self, name: str) -> ConsumerPolicy | None:
        return self._config.consumers.get(name)

    def consumer_names(self) -> list[str]:
        return list(self._config.consumers.keys())

    def update_consumer(self, name: str, updates: dict) -> ConsumerPolicy:
        """Validate and atomically apply a consumer update.

        The update is validated against the schema and detect_types BEFORE it is
        applied. If invalid, a PolicyLoadError is raised and the active config is
        left unchanged (invalid changes must not silently remove protection).
        """
        current = self._config.consumers.get(name)
        if current is None:
            raise PolicyLoadError(f"unknown consumer: {name}")
        # Build a candidate by applying updates to a copy of the current config.
        candidate_data = current.model_dump()
        candidate_data.update(updates)
        try:
            candidate = ConsumerPolicy.model_validate(candidate_data)
            candidate.name = name
            candidate.detect_types_set()  # validates detect_types
        except Exception:  # noqa: BLE001
            # Do not echo the rejected values (review issue 7): a generic message
            # avoids reflecting potentially sensitive input.
            raise PolicyLoadError(
                f"invalid update for consumer '{name}': rejected by validation"
            ) from None
        # Atomic apply: only replace after validation succeeds.
        self._config.consumers[name] = candidate
        return candidate
