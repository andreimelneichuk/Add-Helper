"""Подставной Директ на уровне HTTP: форма запросов и ответов по документации и записям.

Хранит кампании, группы и объявления, отвечает как Директ (ошибки с HTTP 200 в `error`,
результаты по объектам в `*Results`). Сбои внедряются по имени «сервис.метод».
"""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

UNITS = "10/159990/160000"
PASSIVE = {"SERVING_OFF", "NETWORK_DEFAULT"}


@dataclass
class FakeDirect:
    campaigns: dict[int, dict[str, Any]] = field(default_factory=dict)
    groups: dict[int, dict[str, Any]] = field(default_factory=dict)
    ads: dict[int, dict[str, Any]] = field(default_factory=dict)
    keywords: dict[int, dict[str, Any]] = field(default_factory=dict)
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    geo_regions: list[dict[str, Any]] = field(default_factory=list)
    geo_exact_names_empty: bool = False
    report_rows: list[tuple[str, str, int]] = field(default_factory=list)
    amount: str | None = "1500.5"
    status_blocked: bool = False
    status: str | None = None
    no_shared_account: bool = False
    calls: list[str] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    # «сервис.метод» → сколько раз ответить 503; «сервис.метод!» — сбой после применения.
    failures: dict[str, int] = field(default_factory=dict)
    request_errors: dict[str, int] = field(default_factory=dict)
    report_queue: int = 0
    next_id: int = 100

    def _dictionaries_getGeoRegions(self, params: dict[str, Any]) -> dict[str, Any]:
        criteria = params["SelectionCriteria"]
        if criteria.get("ExactNames"):
            names = set(criteria["ExactNames"])
            regions = (
                []
                if self.geo_exact_names_empty
                else [region for region in self.geo_regions if region["GeoRegionName"] in names]
            )
        else:
            query = str(criteria.get("Name") or "").casefold()
            regions = [
                region
                for region in self.geo_regions
                if query in str(region["GeoRegionName"]).casefold()
            ]
        return {"GeoRegions": regions}

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if "live/v4" in str(request.url):
            return self._live(body)
        service = request.url.path.rsplit("/", 1)[-1]
        if service == "reports":
            return self._report(request, body)
        method = body["method"]
        name = f"{service}.{method}"
        self.calls.append(name)
        self.requests.append({"name": name, "headers": dict(request.headers), "body": body})
        if self.failures.get(name):
            self.failures[name] -= 1
            return httpx.Response(503)
        if self.request_errors.get(name):
            code = self.request_errors.pop(name)
            return _json({"error": {"error_code": code, "error_string": "x", "error_detail": "y"}})
        result = getattr(self, f"_{service}_{method}")(body["params"])
        if self.failures.get(name + "!"):
            # Изменение применилось, а ответ потерялся.
            self.failures[name + "!"] -= 1
            return httpx.Response(504)
        return _json({"result": result}, units=UNITS)

    # --- Кампании ---

    def _campaigns_get(self, params: dict[str, Any]) -> dict[str, Any]:
        ids = params["SelectionCriteria"].get("Ids")
        found = [c for i, c in sorted(self.campaigns.items()) if not ids or i in ids]
        return {"Campaigns": found}

    def _campaigns_add(self, params: dict[str, Any]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for item in params["Campaigns"]:
            strategy = item["UnifiedCampaign"]["BiddingStrategy"]
            own = [s for s in strategy.values() if s["BiddingStrategyType"] not in PASSIVE]
            if len(own) > 1:
                # Так ответил настоящий Директ 2026-11-05 на две автостратегии сразу.
                error = {"Code": 4000, "Message": "Параметры запроса указаны неверно"}
                results.append({"Errors": [{**error, "Details": "Стратегии не совместимы"}]})
                continue
            campaign_id = self._id()
            self.campaigns[campaign_id] = {
                "Id": campaign_id,
                "Name": item["Name"],
                "Type": "UNIFIED_CAMPAIGN",
                "State": "OFF",
                "Status": "DRAFT",
                "UnifiedCampaign": {
                    "BiddingStrategy": item["UnifiedCampaign"]["BiddingStrategy"],
                    "CounterIds": item["UnifiedCampaign"].get("CounterIds"),
                },
            }
            results.append({"Id": campaign_id})
        return {"AddResults": results}

    def _campaigns_update(self, params: dict[str, Any]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for item in params["Campaigns"]:
            campaign = self.campaigns.get(item["Id"])
            if campaign is None:
                results.append({"Errors": [{"Code": 8800, "Message": "Объект не найден"}]})
                continue
            if "TimeTargeting" in item:
                campaign["TimeTargeting"] = item["TimeTargeting"]
            for key in ("UnifiedCampaign", "TextCampaign"):
                if key in item:
                    strategy = campaign[key]["BiddingStrategy"]
                    for side, update in item[key]["BiddingStrategy"].items():
                        merged = dict(strategy[side])
                        for param, value in update.items():
                            if isinstance(value, dict):
                                merged[param] = {**merged.get(param, {}), **value}
                            else:
                                merged[param] = value
                        strategy[side] = merged
            results.append({"Id": item["Id"]})
        return {"UpdateResults": results}

    def _set_state(self, params: dict[str, Any], state: str, key: str) -> dict[str, Any]:
        for campaign_id in params["SelectionCriteria"]["Ids"]:
            self.campaigns[campaign_id]["State"] = state
        return {key: [{"Id": i} for i in params["SelectionCriteria"]["Ids"]]}

    def _campaigns_suspend(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._set_state(params, "SUSPENDED", "SuspendResults")

    def _campaigns_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._set_state(params, "ON", "ResumeResults")

    # --- Группы ---

    def _adgroups_get(self, params: dict[str, Any]) -> dict[str, Any]:
        ids = params["SelectionCriteria"]["CampaignIds"]
        return {"AdGroups": [g for g in self.groups.values() if g["CampaignId"] in ids]}

    def _adgroups_add(self, params: dict[str, Any]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for item in params["AdGroups"]:
            group_id = self._id()
            self.groups[group_id] = {
                "Id": group_id,
                "CampaignId": item["CampaignId"],
                "Name": item["Name"],
                "RegionIds": item["RegionIds"],
            }
            results.append({"Id": group_id})
        return {"AddResults": results}

    def _adgroups_update(self, params: dict[str, Any]) -> dict[str, Any]:
        for item in params["AdGroups"]:
            self.groups[item["Id"]]["RegionIds"] = item["RegionIds"]
        return {"UpdateResults": [{"Id": item["Id"]} for item in params["AdGroups"]]}

    # --- Объявления ---

    def _ads_get(self, params: dict[str, Any]) -> dict[str, Any]:
        criteria = params["SelectionCriteria"]
        found = [
            ad
            for ad in self.ads.values()
            if ad["Id"] in criteria.get("Ids", [ad["Id"]])
            and ad["CampaignId"] in criteria.get("CampaignIds", [ad["CampaignId"]])
        ]
        # Как в записи: пустой отбор отдаёт result без ключа Ads.
        return {"Ads": found} if found else {}

    def _ads_add(self, params: dict[str, Any]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for item in params["Ads"]:
            if len(item["TextAd"]["Title"]) > 56:
                results.append({"Errors": [{"Code": 5001, "Message": "Длина заголовка"}]})
                continue
            ad_id = self._id()
            group = self.groups[item["AdGroupId"]]
            self.ads[ad_id] = {
                "Id": ad_id,
                "CampaignId": group["CampaignId"],
                "AdGroupId": item["AdGroupId"],
                "State": "OFF",
                "Status": "DRAFT",
                "StatusClarification": "",
                "TextAd": {k: item["TextAd"][k] for k in ("Title", "Text", "Href")},
            }
            results.append({"Id": ad_id})
        return {"AddResults": results}

    def _ads_moderate(self, params: dict[str, Any]) -> dict[str, Any]:
        for ad_id in params["SelectionCriteria"]["Ids"]:
            self.ads[ad_id]["Status"] = "MODERATION"
        return {"ModerateResults": [{"Id": i} for i in params["SelectionCriteria"]["Ids"]]}

    def _ads_suspend(self, params: dict[str, Any]) -> dict[str, Any]:
        for ad_id in params["SelectionCriteria"]["Ids"]:
            self.ads[ad_id]["State"] = "SUSPENDED"
        return {"SuspendResults": [{"Id": i} for i in params["SelectionCriteria"]["Ids"]]}

    def _ads_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        for ad_id in params["SelectionCriteria"]["Ids"]:
            self.ads[ad_id]["State"] = "ON"
        return {"ResumeResults": [{"Id": i} for i in params["SelectionCriteria"]["Ids"]]}

    # --- Ключевые фразы ---

    def _keywords_get(self, params: dict[str, Any]) -> dict[str, Any]:
        criteria = params.get("SelectionCriteria", {})
        group_ids = criteria.get("AdGroupIds", [])
        found = [
            kw for kw in self.keywords.values() if not group_ids or kw["AdGroupId"] in group_ids
        ]
        return {"Keywords": found} if found else {}

    def _keywords_add(self, params: dict[str, Any]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for item in params.get("Keywords", []):
            kw_id = self._id()
            self.keywords[kw_id] = {
                "Id": kw_id,
                "AdGroupId": item["AdGroupId"],
                "Keyword": item["Keyword"],
                "State": "ON",
                "Status": "ACCEPTED",
            }
            if "AutotargetingCategories" in item:
                self.keywords[kw_id]["AutotargetingCategories"] = item["AutotargetingCategories"]
            results.append({"Id": kw_id})
        return {"AddResults": results}

    # --- Клиенты и маркировка ---

    def _clients_get(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.clients:
            return {"Clients": []}
        org_fields = set(params.get("OrganizationFieldNames") or [])
        clients = []
        for c in self.clients.values():
            client = dict(c)
            if "ErirAttributes" in client and not org_fields:
                client.pop("ErirAttributes", None)
            clients.append(client)
        return {"Clients": clients}

    def _clients_update(self, params: dict[str, Any]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for item in params.get("Clients", []):
            tin_info = item.get("TinInfo") or {}
            tin = tin_info.get("Tin", "")
            if tin == "invalid" or len(tin) not in (10, 12) or not tin.isdigit():
                results.append(
                    {
                        "Errors": [
                            {
                                "Code": 5005,
                                "Message": "Неверно указан ИНН",
                                "Details": "Некорректный формат ИНН",
                            }
                        ]
                    }
                )
                continue
            login = item.get("Login") or "default"
            client_id = item.get("ClientId") or 1000
            current = self.clients.setdefault(login, {"ClientId": client_id, "Login": login})
            current.update(item)
            results.append({"ClientId": client_id})
        return {"UpdateResults": results}

    # --- Отчёты и счёт ---

    def _report(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        self.calls.append("reports")
        self.requests.append({"name": "reports", "headers": dict(request.headers), "body": body})
        if self.report_queue:
            self.report_queue -= 1
            return httpx.Response(201, headers={"retryIn": "2"})
        definition = body["params"]
        fields = definition["FieldNames"]
        campaign_filter = next(
            (
                item
                for item in definition.get("SelectionCriteria", {}).get("Filter", [])
                if item.get("Field") == "CampaignId"
            ),
            None,
        )
        rows = self.report_rows
        if campaign_filter is not None:
            campaign_ids = set(campaign_filter["Values"])
            rows = [row for row in rows if row[1] in campaign_ids]
        lines = ["\t".join(fields)]
        if fields == ["Cost"]:
            lines.append(str(sum(cost for _, _, cost in rows)))
        else:
            for day, campaign_id, cost in rows:
                values = {"Date": day, "CampaignId": campaign_id, "Cost": str(cost)}
                lines.append("\t".join(values[f] for f in fields))
        return httpx.Response(
            200,
            text="\n".join(lines) + "\n",
            headers={"content-type": "text/tab-separated-values; charset=utf-8"},
        )

    def _live(self, body: dict[str, Any]) -> httpx.Response:
        self.calls.append("live." + body["method"])
        if self.no_shared_account:
            return _json({"data": {"Accounts": []}})
        login = body["param"]["SelectionCriteria"]["Logins"][0]
        account_data: dict[str, Any] = {"Login": login, "Amount": self.amount}
        if self.status_blocked:
            account_data["StatusBlocked"] = "Yes"
        if self.status:
            account_data["Status"] = self.status
        return _json({"data": {"Accounts": [account_data]}})


def _json(payload: dict[str, Any], units: str | None = None) -> httpx.Response:
    headers: dict[str, str] = {"Units": units} if units else {}
    return httpx.Response(200, json=payload, headers=headers)
