import tomllib
from importlib.resources import files

from adhelper.creatives.domain.model import CreativeRules


def load_rules(version: str) -> CreativeRules:
    resource = files("adhelper.creatives.adapters") / "rules" / f"{version}.toml"
    data = tomllib.loads(resource.read_text(encoding="utf-8"))
    for key in ("forbidden_words", "fact_words", "sensitive_niches"):
        data[key] = tuple(data[key])
    return CreativeRules(**data)
