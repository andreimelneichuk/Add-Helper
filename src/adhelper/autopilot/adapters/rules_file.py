import tomllib
from importlib.resources import files

from adhelper.autopilot.domain.policy import PolicyRules
from adhelper.autopilot.domain.rules import AutopilotRules


def load_rules(version: str) -> AutopilotRules:
    resource = files("adhelper.autopilot.adapters") / "rules" / f"{version}.toml"
    return AutopilotRules(**tomllib.loads(resource.read_text(encoding="utf-8")))


def load_policy_rules(version: str) -> PolicyRules:
    resource = files("adhelper.autopilot.adapters") / "rules" / f"{version}.toml"
    return PolicyRules(**tomllib.loads(resource.read_text(encoding="utf-8")))
