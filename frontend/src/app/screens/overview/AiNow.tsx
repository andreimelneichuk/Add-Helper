import { rub } from "../../data";
import { fixtures } from "../../fixtures";
import type { State } from "../../store";

function liveCheck(s: State): string {
  const live = s.live!;
  const balance = live.balance === null ? "Сумму на счёте пока не видел." : `На счёте ${rub(live.balance)}.`;
  const check = live.gap === null ? "Сверки со счётом ещё не было." : `Сверка: разница ${rub(live.gap)}.`;
  const limit = live.budget > 0 ? " Лимит стоит в самом Яндексе и сработает, даже если мы остановимся." : "";
  return `${balance} ${check}${limit}`;
}

export function AiNow({ s }: { s: State }) {
  if (s.live) {
    return (
      <section aria-labelledby="ov-now-title" className="ah-panel ov-now">
        <h2 id="ov-now-title" className="ah-panel-title">Счёт в Яндексе</h2>
        <div className="ov-check">{liveCheck(s)}</div>
      </section>
    );
  }
  return <>{fixtures.aiNow(s)}</>;
}
