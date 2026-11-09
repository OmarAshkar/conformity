"""Check directory conformance against a source of truth."""

from conformity.checks import run_checks
from conformity.config import Config, ConfigError, load_config
from conformity.models import CheckResult, Report, RuleKind, Status
from conformity.sync import (
    SyncConfig,
    SyncConfigError,
    SyncError,
    SyncReport,
    load_sync_config,
    sync,
)

__all__ = [
    "CheckResult",
    "Config",
    "ConfigError",
    "Report",
    "RuleKind",
    "Status",
    "SyncConfig",
    "SyncConfigError",
    "SyncError",
    "SyncReport",
    "load_config",
    "load_sync_config",
    "run_checks",
    "sync",
]

__version__ = "0.1.0"
