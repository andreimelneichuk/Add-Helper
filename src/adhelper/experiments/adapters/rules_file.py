import tomllib
from importlib.resources import files

from adhelper.experiments.domain.model import ExperimentRules


def load_rules(version: str) -> ExperimentRules:
    resource = files("adhelper.experiments.adapters") / "rules" / f"{version}.toml"
    return ExperimentRules(**tomllib.loads(resource.read_text(encoding="utf-8")))
