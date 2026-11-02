import tomllib
from importlib.resources import files

from adhelper.creatives.domain.lifecycle import SlotRules
from adhelper.creatives.domain.model import CreativeRules, WizardRules


def load_rules(version: str) -> CreativeRules:
    resource = files("adhelper.creatives.adapters") / "rules" / f"{version}.toml"
    data = tomllib.loads(resource.read_text(encoding="utf-8"))
    for key in ("forbidden_words", "fact_words", "fact_stems", "sensitive_niches"):
        data[key] = tuple(data[key])
    return CreativeRules(**data)


def load_slot_rules(version: str) -> SlotRules:
    resource = files("adhelper.creatives.adapters") / "rules" / f"{version}.toml"
    return SlotRules(**tomllib.loads(resource.read_text(encoding="utf-8")))


def load_wizard_rules(version: str) -> WizardRules:
    resource = files("adhelper.creatives.adapters") / "rules" / f"{version}.toml"
    return WizardRules(**tomllib.loads(resource.read_text(encoding="utf-8")))
