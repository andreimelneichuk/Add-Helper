import tomllib
from importlib.resources import files

from adhelper.business.domain.site import SiteRules

CURRENT_SITE_RULES = "site-1"


def load_site_rules(version: str = CURRENT_SITE_RULES) -> SiteRules:
    resource = files("adhelper.business.adapters") / "rules" / f"{version}.toml"
    raw = tomllib.loads(resource.read_text(encoding="utf-8"))
    return SiteRules(
        version=raw["version"],
        max_pages=raw["max_pages"],
        max_page_bytes=raw["max_page_bytes"],
        max_redirects=raw["max_redirects"],
        timeout_seconds=raw["timeout_seconds"],
        max_text_chars=raw["max_text_chars"],
        min_text_chars=raw["min_text_chars"],
        max_tokens=raw["max_tokens"],
        max_reads_per_day=raw["max_reads_per_day"],
        max_guest_reads_per_address=raw["max_guest_reads_per_address"],
        max_guest_reads_per_day=raw["max_guest_reads_per_day"],
        max_attempts=raw["max_attempts"],
        max_services=raw["max_services"],
        max_features=raw["max_features"],
        link_words=tuple(raw["link_words"]),
        system_prompt=raw["system_prompt"].strip(),
    )
