"""Check directory conformance against a source of truth."""

from conformity.checks import run_checks
from conformity.config import Config, ConfigError, load_config
from conformity.models import CheckResult, Report, RuleKind, Status

__all__ = [
    "CheckResult",
    "Config",
    "ConfigError",
    "Report",
    "RuleKind",
    "Status",
    "load_config",
    "run_checks",
]

__version__ = "0.1.0"
