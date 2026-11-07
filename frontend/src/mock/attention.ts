// Прототип: сообщения «внимания» по ситуациям на подставных данных. В боевую сборку не входят.
import { rub } from "../app/data";
import { fixtures } from "../app/fixtures";
import type { AttentionMessage } from "../app/fixtures";
import { budgetOf, spentOf } from "../app/store";
import type { State, Store } from "../app/store";

export function prototypeAttention(s: State, store: Store, calmOf: (s: State) => AttentionMessage): AttentionMessage {
  if (s.paused) return { tone: "bad", title: "Реклама на паузе", text: "Показы останавливаются в течение 5 минут, настройки сохранены. Деньги не тратятся.", actions: [{ label: "Включить", pick: () => store.run({ action: "pause" }) }] };
  const toHistory = () => store.goView("history");
  switch (s.situation) {
    case "first":
      return { tone: "info", title: `День ${fixtures.DAYS.first.length} из примерно 14: выводы делать рано`, text: `Яндекс проверил объявления, показы идут, первое обращение получено. Первые выводы примерно через ${14 - fixtures.DAYS.first.length} дней.`, actions: [] };
    case "rejected":
      return { tone: "info", title: "Яндекс отклонил объявление, я уже исправил", text: fixtures.TEXT.rejected, actions: [{ label: "Посмотреть в истории", pick: toHistory }] };
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
        title: `Осталось ${rub(left)} на ${30 - fixtures.DAYS.budget.length} дня`,
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
