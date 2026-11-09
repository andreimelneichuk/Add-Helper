import logging
from collections.abc import Sequence
from typing import Protocol

from adhelper.autopilot.domain.review import (
    REPLY_SCHEMA,
    SYSTEM,
    Candidate,
    ReviewResult,
    build_request,
    max_tokens_for,
    nothing_passed,
    parse_reply,
)

logger = logging.getLogger(__name__)


class _Reply(Protocol):
    @property
    def text(self) -> str: ...


class ReviewModel(Protocol):
    """Структурно совместима с `TextModel` из creatives."""

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> _Reply: ...


class ModelReviewer:
    """Рецензент поверх языковой модели; при любом сбое не пропускает ничего."""

    def __init__(self, model: ReviewModel) -> None:
        self._model = model

    async def review(self, candidates: Sequence[Candidate]) -> ReviewResult:
        if not candidates:
            return ReviewResult(frozenset())
        try:
            reply = await self._model.complete_json(
                SYSTEM, build_request(candidates), REPLY_SCHEMA, max_tokens_for(len(candidates))
            )
            return parse_reply(reply.text, candidates)
        except Exception as error:
            # Сюда же: ответ без текста и слишком глубокий JSON. Не пропускаем ничего.
            logger.warning("Рецензент недоступен: %s", type(error).__name__)
            return nothing_passed(candidates, "рецензент недоступен", False)
