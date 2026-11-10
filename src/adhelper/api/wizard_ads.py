"""Шаг «Объявления» мастера: писатель до входа по фактам черновика, выбор после входа.

Набор пишет воркер тем же писателем, что и для направлений: модель, проверка кодом и судья.
До входа набор видит только браузер с ключом; после входа выбранные объявления
переходят в бизнес, и запуск (T6) начнёт с них.
"""

import secrets
from collections.abc import Mapping
from hashlib import sha256
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from adhelper.accounts.adapters.sql import account_states
from adhelper.accounts.application.service import AccountState
from adhelper.api.cabinet import Owned, OwnedChecked, _now, _sessions
from adhelper.api.control import _ledger
from adhelper.api.site import _guest_key, _token_hash
from adhelper.campaigns.application.launch import generate_keywords
from adhelper.creatives.adapters.rules_file import load_wizard_rules
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.adapters.wizard import (
    SqlWizardAds,
    SqlWizardSets,
    TooManyGuestSets,
    TooManySets,
)
from adhelper.creatives.application.service import CURRENT_RULES
from adhelper.creatives.application.wizard import (
    CURRENT_WIZARD_RULES,
    CreativeSource,
    DraftProblem,
    SetStatus,
    WizardDraft,
    WizardSet,
    draft_profile,
    kept_is_valid,
)
from adhelper.ledger.adapters.sql import SqlChangeRepository
from adhelper.ledger.application.facade import (
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    ProposedChange,
    failure_text,
)

DRAFT_PROBLEMS = {
    DraftProblem.NO_SERVICES: "Добавьте хотя бы одну услугу: объявления пишу по услугам",
    DraftProblem.TOO_MANY: "Фактов слишком много: оставьте главные услуги и особенности",
    DraftProblem.TOO_LONG: "Один из фактов слишком длинный: сократите его",
}
SET_PROBLEM = "Не получилось написать объявления. Попробуйте ещё раз"
MAX_KEPT = 50


class ServiceBody(BaseModel):
    name: str = Field(max_length=2000)
    price: int | None = Field(default=None, ge=0, le=100_000_000)


class DraftBody(BaseModel):
    niche: str = Field(default="", max_length=2000)
    address: str = Field(default="", max_length=2000)
    services: list[ServiceBody] = Field(default_factory=list, max_length=200)
    features: list[str] = Field(default_factory=list, max_length=200)
    variants: int = Field(default=5, ge=1, le=10)
    force: bool = False


class AdoptBody(BaseModel):
    id: int
    token: str = Field(min_length=1, max_length=200)
    kept: list[int] = Field(max_length=MAX_KEPT)


class WizardAdBody(BaseModel):
    headline: str = Field(min_length=1, max_length=56)
    text: str = Field(min_length=1, max_length=81)

    @field_validator("headline", "text")
    @classmethod
    def check_not_empty_after_strip(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Поле не может быть пустым или состоять только из пробелов")
        return s


class WizardAdStateBody(BaseModel):
    active: bool


class CreateWizardAdBody(WizardAdBody):
    active: bool = True
    client_id: str | None = Field(default=None, min_length=1, max_length=200)


class WizardAdArchiveBody(BaseModel):
    archived: bool = True


class WizardAdPublishBody(BaseModel):
    grouping: str = Field(default="services", pattern="^(services|general)$")


def _draft(draft: WizardDraft) -> dict[str, Any]:
    return {
        "headline": draft.headline,
        "text": draft.text,
        "facts": [source.label for source in draft.sources],
    }


def _set_body(found: WizardSet) -> dict[str, Any]:
    failed = found.status is SetStatus.FAILED
    return {
        "id": found.id,
        "status": found.status.value,
        # Шаблон собран кодом из фактов: модель не ответила или все её тексты сняты.
        "source": None if found.source is None else found.source.value,
        "template": found.source is CreativeSource.TEMPLATE,
        "needs_review": found.needs_review,
        "problem_text": SET_PROBLEM if failed else None,
        "ads": [_draft(d) for d in found.drafts],
    }


def guest_ads_router() -> APIRouter:
    """Шаг 4 мастера до входа. Набор видит только этот браузер."""
    router = APIRouter(prefix="/api/creative-sets")

    # Тело только JSON: чужая страница не отправит его без предварительного запроса.
    @router.post("", status_code=status.HTTP_202_ACCEPTED)
    async def post_guest_set(request: Request, body: DraftBody) -> dict[str, Any]:
        rules = load_wizard_rules(CURRENT_WIZARD_RULES)
        profile = draft_profile(
            body.niche,
            body.address,
            [(item.name, item.price) for item in body.services],
            body.features,
            rules,
        )
        if isinstance(profile, DraftProblem):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, DRAFT_PROBLEMS[profile])
        token = secrets.token_urlsafe(32)
        try:
            set_id = await SqlWizardSets(_sessions(request)).request(
                profile,
                _guest_key(request),
                _token_hash(token),
                rules,
                CURRENT_RULES,
                _now(request),
                force=body.force,
                wanted=body.variants,
            )
        except TooManySets:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "С вашего устройства (IP-сети) объявления сегодня писали уже"
                f" {rules.max_guest_sets_per_address} раза."
                " Оставьте те, что есть, или попробуйте завтра",
            ) from None
        except TooManyGuestSets:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Сейчас писатель объявлений перегружен. Попробуйте позже",
            ) from None
        return {"id": set_id, "token": token, "status": "queued"}

    @router.get("/{set_id}")
    async def get_guest_set(
        request: Request,
        set_id: int,
        x_set_token: Annotated[str | None, Header(max_length=200)] = None,
    ) -> dict[str, Any]:
        found = None
        if x_set_token:
            found = await SqlWizardSets(_sessions(request)).get(set_id, _token_hash(x_set_token))
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявления не найдены")
        return _set_body(found)

    return router


def wizard_ads_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.post("/creative-sets/adopt")
    async def adopt(request: Request, body: AdoptBody, business_id: OwnedChecked) -> dict[str, Any]:
        sets = SqlWizardSets(_sessions(request))
        token_hash = _token_hash(body.token)
        found = await sets.ready(body.id, token_hash, business_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявления не найдены")
        if found.status is not SetStatus.READY:
            raise HTTPException(status.HTTP_409_CONFLICT, "Объявления ещё пишутся")
        kept = tuple(body.kept)
        if not kept_is_valid(kept, len(found.drafts)):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Оставьте хотя бы одно объявление из написанных",
            )
        if not await sets.adopt(body.id, token_hash, business_id, kept):
            raise HTTPException(status.HTTP_409_CONFLICT, "Объявления ещё пишутся")
        return {"kept": len(kept)}

    @router.get("/wizard-ads")
    async def wizard_ads(
        request: Request,
        business_id: Owned,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        sessions = _sessions(request)
        wizard = SqlWizardAds(sessions, business_id)
        all_items = await wizard.all_ads()
        items = (
            all_items if include_inactive else [i for i in all_items if i.active and not i.archived]
        )
        ads_list = [
            {
                "id": item.id,
                "headline": item.headline,
                "text": item.text,
                "facts": list(item.facts),
                "active": item.active,
                "archived": item.archived,
                "submitted": item.submitted,
                "publication_status": item.publication_status,
                "services": list(item.services),
            }
            for item in items
        ]
        profiles = await SqlProfiles(sessions, business_id).get()
        keywords: list[str] = []
        if profiles:
            keywords = list(
                generate_keywords(
                    profiles[0].services,
                    niche=profiles[0].niche,
                    address=profiles[0].address,
                )
            )
        res: dict[str, Any] = {"ads": ads_list}
        if keywords:
            res["keywords"] = keywords
        return res

    @router.post("/wizard-ads", status_code=status.HTTP_201_CREATED)
    async def post_wizard_ad(
        request: Request, body: CreateWizardAdBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        sessions = _sessions(request)
        wizard = SqlWizardAds(sessions, business_id)
        item = await wizard.add_ad(
            body.headline.strip(),
            body.text.strip(),
            _now(request),
            active=body.active,
            client_id=body.client_id,
        )
        return {
            "id": item.id,
            "headline": item.headline,
            "text": item.text,
            "facts": list(item.facts),
            "active": item.active,
            "archived": item.archived,
        }

    @router.post("/wizard-ads/{ad_id}/publish")
    async def publish_wizard_ad(
        request: Request, ad_id: str, body: WizardAdPublishBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        """Отправляет сохранённый черновик в последнюю кампанию через журнал изменений."""
        idx_str = ad_id.removeprefix("w")
        if not idx_str.isdigit():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        item = next(
            (
                ad
                for ad in await SqlWizardAds(_sessions(request), business_id).all_ads()
                if ad.id == f"w{idx_str}"
            ),
            None,
        )
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        if item.publication_status is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Объявление уже отправлено в Яндекс")
        if not item.active or item.archived:
            raise HTTPException(status.HTTP_409_CONFLICT, "Объявление отключено или в архиве")

        sessions = _sessions(request)
        repo = SqlChangeRepository(sessions, business_id)
        connected = [
            login
            for login, state in await account_states(sessions, business_id)
            if state is AccountState.CONNECTED
        ]
        candidates: list[tuple[int, str, str, Mapping[str, Any]]] = []
        for login in connected:
            for target in await repo.campaign_targets_for(sessions, business_id, login):
                changes = await repo.on_target(login, target)
                applied = [
                    change
                    for change in changes
                    if change.kind is ChangeKind.CREATE_CAMPAIGN
                    and change.status is ChangeStatus.APPLIED
                    and change.after
                ]
                if applied:
                    campaign_change = max(applied, key=lambda row: row.id)
                    if campaign_change.after is not None:
                        candidates.append(
                            (
                                campaign_change.id,
                                login,
                                str(campaign_change.after["campaign_id"]),
                                campaign_change.planned,
                            )
                        )
        if not candidates:
            raise HTTPException(status.HTTP_409_CONFLICT, "Сначала запустите кампанию в Директе")
        _, login, campaign_id, plan = max(candidates, key=lambda candidate: candidate[0])
        platform = request.app.state.platform_for(business_id, login)
        if platform is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Кабинет Директа не подключён")

        profile_rows = await SqlProfiles(sessions, business_id).get()
        profile = profile_rows[0] if profile_rows else None
        services = tuple(
            service
            for service in (profile.services if profile else ())
            if body.grouping == "services" and service.name in item.services
        )
        group_name = (
            " + ".join(service.name for service in services) if services else "Общие услуги"
        )
        keywords = tuple(
            generate_keywords(services, niche=profile.niche, address=profile.address)
            if services and profile
            else ()
        )
        ad_digest = sha256(item.headline.encode() + b"\0" + item.text.encode()).hexdigest()[:10]
        marker = f"wizard-{business_id.hex[:8]}-{idx_str}-{ad_digest}"
        proposed = ProposedChange(
            account=login,
            source=ChangeSource.USER,
            kind=ChangeKind.CREATE_AD,
            target_ref=f"newad:{marker}",
            planned={
                "campaign_id": campaign_id,
                "headline": item.headline,
                "text": item.text,
                "href": str(plan.get("href") or ""),
                "group_name": group_name,
                "keywords": list(keywords),
            },
            explanation=(
                f"Отправил объявление «{item.headline}» в группу «{group_name}» "
                "на проверку Яндексу."
                " Лимит кампании не меняется; показы начнутся после одобрения."
            ),
            idempotency_key=f"wizard-publish:{business_id.hex}:{idx_str}:{marker}",
        )
        ledger = _ledger(request, business_id, platform)
        submitted = await ledger.submit(proposed)
        await ledger.execute_pending(login)
        final = await ledger.get_change(submitted.id)
        if final is not None and final.status in {
            ChangeStatus.FAILED,
            ChangeStatus.REJECTED,
            ChangeStatus.CONFLICT,
        }:
            return {
                "status": "refused",
                "group": group_name,
                "text": failure_text(final.failure_reason),
            }
        return {
            "status": "applied" if final and final.status is ChangeStatus.APPLIED else "pending",
            "group": group_name,
            "text": None,
        }

    @router.put("/wizard-ads/{ad_id}")
    async def put_wizard_ad(
        request: Request, ad_id: str, body: WizardAdBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        sessions = _sessions(request)
        wizard = SqlWizardAds(sessions, business_id)
        idx_str = ad_id.removeprefix("w")
        if not idx_str.isdigit():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        item = await wizard.update_ad(int(idx_str), body.headline.strip(), body.text.strip())
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        return {
            "id": item.id,
            "headline": item.headline,
            "text": item.text,
            "facts": list(item.facts),
            "active": item.active,
            "archived": item.archived,
        }

    @router.post("/wizard-ads/{ad_id}/state")
    async def post_wizard_ad_state(
        request: Request, ad_id: str, body: WizardAdStateBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        sessions = _sessions(request)
        wizard = SqlWizardAds(sessions, business_id)
        idx_str = ad_id.removeprefix("w")
        if not idx_str.isdigit():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        ok = await wizard.set_active(int(idx_str), body.active)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        return {"status": "applied", "active": body.active}

    @router.post("/wizard-ads/{ad_id}/archive")
    async def post_wizard_ad_archive(
        request: Request, ad_id: str, body: WizardAdArchiveBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        sessions = _sessions(request)
        wizard = SqlWizardAds(sessions, business_id)
        idx_str = ad_id.removeprefix("w")
        if not idx_str.isdigit():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        ok = await wizard.set_archived(int(idx_str), body.archived)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        return {"status": "applied", "archived": body.archived, "active": not body.archived}

    @router.delete("/wizard-ads/{ad_id}")
    async def delete_wizard_ad(
        request: Request, ad_id: str, business_id: OwnedChecked
    ) -> dict[str, Any]:
        sessions = _sessions(request)
        wizard = SqlWizardAds(sessions, business_id)
        idx_str = ad_id.removeprefix("w")
        if not idx_str.isdigit():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        ok = await wizard.set_archived(int(idx_str), True)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        return {"status": "archived", "archived": True}

    return router
