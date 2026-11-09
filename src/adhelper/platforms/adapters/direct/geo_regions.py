"""Deterministic resolution helpers for the Direct GeoRegions tree."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

RUSSIA_ID = 225


@dataclass(frozen=True)
class GeoRegionChoice:
    region_id: int
    city: str
    display: str
    subject: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "city": self.city,
            "display": self.display,
            "subject": self.subject,
        }


def normalize(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def city_query(address: str) -> str:
    """Extract a city token from a business address without guessing from arbitrary text."""
    explicit = re.search(r"(?:г(?:ород)?\.?\s*)([^,;]+)", address.strip(), re.IGNORECASE)
    query = explicit.group(1).strip() if explicit else address.split(",", 1)[0].strip()
    if not explicit and re.search(r"\b(?:ул\.?|улица|проспект|пр-т|дом|д\.)\b", query, re.I):
        return ""
    return query


def resolve_city_choices(
    address: str, regions: Sequence[Mapping[str, Any]]
) -> tuple[GeoRegionChoice, ...]:
    """Return exact Russian City matches, with the nearest federation subject for display.

    If the address itself names exactly one candidate's administrative chain, return only that
    candidate. Otherwise callers must ask the user to choose among all matches.
    """
    query = city_query(address)
    if not query:
        return ()
    by_id: dict[int, Mapping[str, Any]] = {}
    for row in regions:
        try:
            by_id[int(row["GeoRegionId"])] = row
        except (KeyError, TypeError, ValueError):
            continue

    found: list[GeoRegionChoice] = []
    for row in regions:
        if row.get("GeoRegionType") != "City" or normalize(
            str(row.get("GeoRegionName", ""))
        ) != normalize(query):
            continue
        try:
            region_id = int(row["GeoRegionId"])
        except (KeyError, TypeError, ValueError):
            continue
        chain: list[Mapping[str, Any]] = [row]
        seen = {region_id}
        parent_id = row.get("ParentId")
        while parent_id is not None:
            try:
                parent = by_id.get(int(parent_id))
            except (TypeError, ValueError):
                break
            if parent is None or int(parent["GeoRegionId"]) in seen:
                break
            seen.add(int(parent["GeoRegionId"]))
            chain.append(parent)
            parent_id = parent.get("ParentId")
        if not any(int(item["GeoRegionId"]) == RUSSIA_ID for item in chain):
            continue
        subject = next(
            (
                str(item.get("GeoRegionName", ""))
                for item in chain[1:]
                if item.get("GeoRegionType") == "Federation subject"
            ),
            "Россия",
        )
        found.append(
            GeoRegionChoice(
                region_id, str(row["GeoRegionName"]), f"{row['GeoRegionName']}, {subject}", subject
            )
        )

    if len(found) < 2:
        return tuple(found)
    context = normalize(address)
    narrowed = [choice for choice in found if normalize(choice.subject) in context]
    return tuple(narrowed) if len(narrowed) == 1 else tuple(found)
