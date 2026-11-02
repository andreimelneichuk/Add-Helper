import { DAYS, leadsWord, rub } from "../../data";
import { budgetOf, spentOf, week } from "../../store";
import type { State, Store } from "../../store";

type Tone = "info" | "bad" | "warn" | "good";
type Decision = { label: string; pick: () => void; quiet?: boolean };
/** calm: спокойный итог, экранному чтению о нём объявлять незачем. */
type Message = { tone: Tone; title: string; text: string; actions: Decision[]; calm?: boolean };

/** Вывод, когда вмешиваться не нужно. Числа подставляет код. */
function calmOf(s: State): Message {
  const w = week(s);
  const was = w.prevLeads > 0 ? `Неделей раньше было ${w.prevLeads} по ${rub(w.prevCost / w.prevLeads)}. ` : "";
  return { tone: "good", title: `Всё в порядке: ${w.leads} ${leadsWord(w.leads)} за 7 дней, по ${rub(w.cost / Math.max(1, w.leads))}`, text: `${was}Вмешиваться не нужно.`, actions: [], calm: true };
}

function liveMessage(s: State, store: Store): Message {
  const first = s.live!.alerts[0];
  const toHistory = () => store.goView("history");
  if (first) {
    const others = s.live!.alerts.slice(1).map((a) => a.title).join(". ");
    const actions: Decision[] = first.kind === "balance_mismatch" ? [{ label: "Я знаю причину", pick: () => store.ask({ action: "balance" }) }, { label: "Что вы сделали", pick: toHistory, quiet: true }] : [];
    return { tone: first.tone, title: first.title, text: others ? `${first.text} Ещё: ${others}.` : first.text, actions };
  }
  if (s.paused) return { tone: "bad", title: "Реклама на паузе", text: "Настройки сохранены, деньги не тратятся.", actions: [] };
  const w = week(s);
  if (w.leads === 0) return { tone: "info", title: w.cost > 0 ? "Обращений за 7 дней пока нет" : "За 7 дней показов не было", text: w.cost > 0 ? `Потрачено ${rub(w.cost)}. Данных пока мало для выводов.` : "Данных пока нет, поэтому выводов тоже нет.", actions: [], calm: true };
  return calmOf(s);
}

function messageOf(s: State, store: Store): Message {
  if (s.live) return liveMessage(s, store);
  if (s.paused) return { tone: "bad", title: "Реклама на паузе", text: "Показы останавливаются в течение 5 минут, настройки сохранены. Деньги не тратятся.", actions: [{ label: "Включить", pick: () => store.run({ action: "pause" }) }] };
  const toHistory = () => store.goView("history");
  switch (s.situation) {
    case "first":
      return { tone: "info", title: `День ${DAYS.first.length} из примерно 14: выводы делать рано`, text: `Яндекс проверил объявления, показы идут, первое обращение получено. Первые выводы примерно через ${14 - DAYS.first.length} дней.`, actions: [] };
    case "rejected":
      return { tone: "info", title: "Яндекс отклонил объявление, я уже исправил", text: "В «Маникюр от 1 200 ₽» не хватало адреса. Добавил его из профиля и жду повторной проверки. От вас ничего не нужно.", actions: [{ label: "Посмотреть в истории", pick: toHistory }] };
    case "access":
      if (s.topped) return calmOf(s);
      return { tone: "bad", title: "Пропал доступ к кабинету Яндекса", text: "Я остановил все изменения. Лимиты стоят в самом кабинете, поэтому перерасхода не будет.", actions: [{ label: "Подключить заново", pick: store.reconnect }] };
    case "problem":
      if (s.topped) return { tone: "info", title: "Жду, когда деньги дойдут", text: "Как только они появятся на счёте, включу показы сам, обычно за 5 минут.", actions: [], calm: true };
      return { tone: "bad", title: "На счёте в Яндексе 0 ₽, показы стоят", text: "Откройте кабинет под логином salon-primer и пополните счёт. Я включу показы сам.", actions: [{ label: "Я пополнил", pick: store.reportTopUp }] };
    case "budget": {
      if (s.decided === "kept") return { tone: "info", title: "Бюджет оставили как есть", text: "Показы сами остановятся примерно 29 ноября и включатся 1 декабря. Перерасхода не будет.", actions: [], calm: true };
      if (s.decided) return calmOf(s);
      const left = Math.max(0, budgetOf(s) - spentOf(s));
      return {
        tone: "warn",
        title: `Осталось ${rub(left)} на ${30 - DAYS.budget.length} дня`,
        text: "Показы сами остановятся примерно 29 ноября и включатся 1 декабря. Перерасхода не будет.",
        actions: [{ label: "Добавить 3 000 ₽", pick: store.addBudget }, { label: "Оставить", pick: store.keepBudget, quiet: true }],
      };
    }
    case "verdict":
      if (s.undone.verdict) return calmOf(s);
      return { tone: "good", title: "Проверка закончена: скидка лучше цены", text: "11 обращений по 330 ₽ против 4 по 880 ₽. Уверенность высокая, второй вариант отключён.", actions: [{ label: "Посмотреть и отменить", pick: toHistory }] };
    case "running":
      return calmOf(s);
  }
}

export function Attention({ s, store }: { s: State; store: Store }) {
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
            <button key={a.label} type="button" className={a.quiet ? "ah-btn" : "ah-btn ov-attention-do"} onClick={a.pick}>{a.label}</button>
          ))}
        </div>
      )}
    </section>
  );
}
