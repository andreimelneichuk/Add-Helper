import tomllib
from importlib.resources import files
from typing import Any

from adhelper.business.domain.site import RenderRules, SiteRules
from adhelper.business.domain.tag import TagRules

CURRENT_SITE_RULES = "site-3"


def _render(raw: dict[str, Any] | None) -> RenderRules | None:
    if raw is None:
        return None
    return RenderRules(
        below_chars=raw["below_chars"],
        markers=tuple(raw["markers"]),
        timeout_seconds=raw["timeout_seconds"],
        site_budget_seconds=raw["site_budget_seconds"],
        max_requests=raw["max_requests"],
        max_resource_bytes=raw["max_resource_bytes"],
        max_total_bytes=raw["max_total_bytes"],
        max_frames=raw["max_frames"],
        blocked_types=tuple(raw["blocked_types"]),
        blocked_hosts=tuple(raw["blocked_hosts"]),
    )


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
        render=_render(raw.get("render")),
    )


CURRENT_TAG_RULES = "tag-1"


def load_tag_rules(version: str = CURRENT_TAG_RULES) -> TagRules:
    resource = files("adhelper.business.adapters") / "rules" / f"{version}.toml"
    raw = tomllib.loads(resource.read_text(encoding="utf-8"))
    return TagRules(raw["version"], raw["max_checks_per_day"], raw["max_attempts"])
