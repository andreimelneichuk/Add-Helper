import tomllib
from importlib.resources import files

from adhelper.autopilot.domain.outcomes import OutcomeRules
from adhelper.autopilot.domain.policy import PolicyRules
from adhelper.autopilot.domain.rules import AutopilotRules
from adhelper.autopilot.domain.shadow_report import GateThresholds


def load_rules(version: str) -> AutopilotRules:
    resource = files("adhelper.autopilot.adapters") / "rules" / f"{version}.toml"
    return AutopilotRules(**tomllib.loads(resource.read_text(encoding="utf-8")))


def load_policy_rules(version: str) -> PolicyRules:
    resource = files("adhelper.autopilot.adapters") / "rules" / f"{version}.toml"
    return PolicyRules(**tomllib.loads(resource.read_text(encoding="utf-8")))


def load_outcome_rules(version: str) -> OutcomeRules:
    resource = files("adhelper.autopilot.adapters") / "rules" / f"{version}.toml"
    return OutcomeRules(**tomllib.loads(resource.read_text(encoding="utf-8")))


def load_gate_thresholds(version: str) -> GateThresholds:
    resource = files("adhelper.autopilot.adapters") / "rules" / f"{version}.toml"
    return GateThresholds(**tomllib.loads(resource.read_text(encoding="utf-8")))
