import { rub } from "../../data";
import { fixtures } from "../../fixtures";
import { adName } from "../../store";
import type { State } from "../../store";

// Прототип: в основе нет данных идущей проверки и суммы на счёте после пополнения.
const TEST = { discount: 8, price: 3 };
const ACCOUNT_AFTER_TOP_UP = 7200;

function RunningTest({ s }: { s: State }) {
  const total = TEST.discount + TEST.price;
  const rows: [string, number, string][] = [
    [adName(s, "a1"), TEST.discount, "ov-test-fill"],
    [adName(s, "a2"), TEST.price, "ov-test-fill ov-test-fill-soft"],
  ];
  return (
    <div>
      <div className="ov-test-title">Проверяю: скидка или цена, день 9 из 14</div>
      {rows.map(([name, leads, fill]) => (
        <div key={name} className="ov-test-row">
          <div className="ov-test-line"><span>{name}</span><strong>{leads}</strong></div>
          <div className="ov-test-track" aria-hidden="true"><div className={fill} style={{ width: `${Math.round((leads * 100) / total)}%` }} /></div>
        </div>
      ))}
      <div className="ah-muted">Обращения за 9 дней проверки. Данных пока мало, решу на 14-й день.</div>
    </div>
  );
}

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
  const sit = s.situation;
  return (
    <section aria-labelledby="ov-now-title" className="ah-panel ov-now">
      <h2 id="ov-now-title" className="ah-panel-title">{sit === "running" ? "ИИ сейчас" : "Счёт в Яндексе"}</h2>
      {sit === "running" && <RunningTest s={s} />}
      <div className="ov-check">{`На счёте ${rub(s.topped ? ACCOUNT_AFTER_TOP_UP : fixtures.ACCOUNT[sit])}. Сверка: разница 0 ₽, сегодня в 9:40. Лимит стоит в самом Яндексе и сработает, даже если мы остановимся.`}</div>
    </section>
  );
}
