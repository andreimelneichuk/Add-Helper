import type { TodayCard } from "../../../api";
import { leadsWord, rub } from "../../data";
import { fixtures } from "../../fixtures";
import type { AttentionDecision, AttentionMessage } from "../../fixtures";
import { week } from "../../store";
import type { State, Store } from "../../store";

type Decision = AttentionDecision;
type Message = AttentionMessage;

/** Вывод, когда вмешиваться не нужно. Числа подставляет код. */
function calmOf(s: State): Message {
  const w = week(s);
  const was = w.prevLeads > 0 ? `Неделей раньше было ${w.prevLeads} по ${rub(w.prevCost / w.prevLeads)}. ` : "";
  return { tone: "good", title: `Всё в порядке: ${w.leads} ${leadsWord(w.leads)} за 7 дней, по ${rub(w.cost / Math.max(1, w.leads))}`, text: `${was}Вмешиваться не нужно.`, actions: [], calm: true };
}

function liveMessage(s: State, store: Store): Message {
  const first = s.live?.alerts?.[0];
  const toHistory = () => store.goView("history");
  if (first) {
    const others = s.live!.alerts.slice(1).map((a) => a.title).join(". ");
    const actions: Decision[] =
      first.kind === "balance_mismatch" ? [{ label: "Я знаю причину", pick: () => store.ask({ action: "balance" }) }, { label: "Что вы сделали", pick: toHistory, quiet: true }]
        : first.kind === "ads_need_confirmation" || first.kind === "ads_rejected" ? [{ label: "Посмотреть объявления", pick: () => store.goView("ads") }]
          : first.kind === "ads_no_profile" ? [{ label: "Заполнить профиль", pick: () => store.goView("settings") }]
            : first.kind === "platform_not_connected" ? [{ label: "Подключить кабинет", pick: () => store.goView("settings") }]
              : [];
    return { tone: first.tone, title: first.title, text: others ? `${first.text} Ещё: ${others}.` : first.text, actions };
  }
  if (s.paused) return { tone: "bad", title: "Реклама на паузе", text: "Вы поставили паузу. Настройки сохранены, деньги не тратятся. Перед включением проверю, что расход не выйдет за бюджет.", actions: [{ label: "Включить", pick: () => store.run({ action: "pause" }) }] };
  const w = week(s);
  if (w.leads === 0) return { tone: "info", title: w.cost > 0 ? "Обращений за 7 дней пока нет" : "За 7 дней показов не было", text: w.cost > 0 ? `Потрачено ${rub(w.cost)}. Данных пока мало для выводов.` : "Данных пока нет, поэтому выводов тоже нет.", actions: [], calm: true };
  return calmOf(s);
}

/** Действия карточки из закрытого набора сервиса: экран знает, что по ним открыть. */
function cardMessage(card: TodayCard, store: Store): Message {
  const toHistory = () => store.goView("history");
  const actions: Decision[] =
    card.action === "balance" ? [{ label: "Я знаю причину", pick: () => store.ask({ action: "balance" }) }, { label: "Что вы сделали", pick: toHistory, quiet: true }]
      : card.action === "ads" ? [{ label: "Посмотреть объявления", pick: () => store.goView("ads") }]
        : card.action === "settings" ? [{ label: card.kind === "alert:platform_not_connected" ? "Подключить кабинет" : card.kind === "alert:ads_no_profile" ? "Заполнить профиль" : "Открыть настройки", pick: () => store.goView("settings") }]
          : card.action === "resume" ? [{ label: "Включить", pick: () => store.run({ action: "pause" }) }]
            : card.action === "undo" && card.ref ? [{ label: "Отменить", pick: () => { store.goView("history"); store.askUndo(card.ref!); }, quiet: true }]
              : card.action === "history" ? [{ label: "Открыть историю", pick: toHistory }]
                : card.action === "test_offer" && card.ref ? [
                  { label: "Проверить вариант", pick: () => store.run({ action: "test", value: card.ref!, answer: "agree" }) },
                  { label: "Не сейчас", pick: () => store.run({ action: "test", value: card.ref!, answer: "decline" }), quiet: true },
                ]
                  : card.action === "test_cancel" && card.ref ? [{ label: "Отменить тест", pick: () => store.run({ action: "test", value: card.ref!, answer: "cancel" }), quiet: true }]
                    : [];
  return { tone: card.tone, title: card.title, text: card.text, actions, calm: card.kind === "summary" || card.kind === "last_action" || card.kind.startsWith("test_") };
}

function cardsOf(s: State): TodayCard[] {
  const cards = s.live?.cards ?? [];
  return s.live && (s.live.stats?.week?.shows ?? 0) > 0
    ? cards.filter(
        (card) => !(card.kind === "summary" && card.title === "За 7 дней показов не было"),
      )
    : cards;
}

function messageOf(s: State, store: Store): Message {
  if (s.live) {
    const cards = cardsOf(s);
    return cards.length ? cardMessage(cards[0]!, store) : liveMessage(s, store);
  }
  return fixtures.attention(s, store, calmOf);
}

/** Остальные карточки ленты сервиса, по порядку из его правил. Лента конечна. */
function More({ cards, store }: { cards: TodayCard[]; store: Store }) {
  return (
    <section className="ah-panel ov-more" aria-label="Ещё на сегодня">
      <ul>
        {cards.map((card) => {
          const m = cardMessage(card, store);
          return (
            <li key={card.kind + (card.ref ?? "")} className={`ov-more-item ov-more-${card.tone}`}>
              <div className="ov-more-body"><h3>{m.title}</h3>{m.text && <p>{m.text}</p>}</div>
              {m.actions.map((a) => <button key={a.label} type="button" className="ah-btn" disabled={!!store.state.working} onClick={a.pick}>{a.label}</button>)}
            </li>
          );
        })}
      </ul>
      <p className="ov-more-done">На сегодня всё.</p>
    </section>
  );
}

export function Attention({ s, store }: { s: State; store: Store }) {
  const rest = cardsOf(s).slice(1);
  return (
    <>
      <Main s={s} store={store} />
      {rest.length > 0 && <More cards={rest} store={store} />}
    </>
  );
}

function Main({ s, store }: { s: State; store: Store }) {
  const message = messageOf(s, store);
  return (
    <section role={message.calm ? undefined : "status"} className={`ov-attention ov-attention-${message.tone}`}>
      <div className="ov-attention-body">
        <h2>{message.title}</h2>
        <p>{message.text}</p>
      </div>
      {message.actions.length > 0 && (
        <div className="ov-attention-actions">
          {message.actions.map((a) => (
            <button key={a.label} type="button" className={a.quiet ? "ah-btn" : "ah-btn ov-attention-do"} disabled={!!s.working} onClick={a.pick}>{a.label}</button>
          ))}
        </div>
      )}
    </section>
  );
}
