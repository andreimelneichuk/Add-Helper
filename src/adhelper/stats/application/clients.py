"""Ответ владельца, сколько обращений стали клиентами. Это данные для цены клиента, не решение."""

from datetime import date

from adhelper.stats.domain.model import ClientAnswer, ClientAnswers


class NoLeads(Exception):
    """За окно дат обращений не было."""


class TooManyClients(Exception):
    def __init__(self, leads: int) -> None:
        super().__init__(leads)
        self.leads = leads


async def record_clients(
    store: ClientAnswers, since: date, until: date, clients: int
) -> ClientAnswer:
    """Число обращений берём из статистики площадки, а не со слов владельца."""
    leads = await store.leads(since, until)
    if leads == 0:
        raise NoLeads
    if clients > leads:
        raise TooManyClients(leads)
    answer = ClientAnswer(since, until, leads, clients)
    await store.save_clients(answer)
    return answer
