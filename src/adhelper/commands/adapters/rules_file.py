import tomllib
from importlib.resources import files

from adhelper.commands.domain.model import CommandRules


def load_rules(version: str) -> CommandRules:
    resource = files("adhelper.commands.adapters") / "rules" / f"{version}.toml"
    return CommandRules(**tomllib.loads(resource.read_text(encoding="utf-8")))
