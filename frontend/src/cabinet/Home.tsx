import { useEffect, useState } from "react";
import type { Alert, Api, HistoryLine, Money, Overview } from "../api";
import { count, dateTime, moscowDay, percent, rub, rubExact } from "../format";
import type { Section } from "./Cabinet";
import { PeriodNote } from "./Cabinet";
import { DailyChart } from "./Chart";
import { CampaignTable } from "./Campaigns";
import { describeChange } from "./HistoryScreen";
import { ALERT } from "./words";

type Props = { api: Api; overview: Overview; onChanged: () => void; go: (s: Section) => void };

export function Home({ api, overview, onChanged, go }: Props) {
  const [recent, setRecent] = useState<HistoryLine[] | null>(null);
  useEffect(() => {
    api.history().then((lines) => setRecent(lines.slice(0, 4)), () => setRecent([]));
  }, [api, overview]);
  const money = overview.money[0];
  const { totals, ratios } = overview;

  return (
    <>
      <div className="cab-head">
        <h1>Сводка</h1>
        <PeriodNote overview={overview} />
      </div>

      <section aria-labelledby="status">
        <h2 id="status" className="cab-visually-hidden">Всё ли в порядке</h2>
        {overview.alerts.length === 0 ? (
          <div className="cab-alert cab-alert-ok" role="status">
            <strong>Реклама работает.</strong> Проблем нет: счёт сходится со статистикой, бюджет месяца под защитой.
          </div>
        ) : (
          overview.alerts.map((alert) => (
            <AlertCard key={alert.kind} alert={alert} money={money} api={api} onChanged={onChanged} go={go} />
          ))
        )}
      </section>

      <div className="cab-grid2">
        <div className="cab-col">
          <section className="cab-panel" aria-labelledby="results">
            <h2 id="results">Что получено за 14 дней</h2>
            <dl className="cab-kpis">
              <Kpi label="Расход" value={rub(totals.cost)} hint="по отчётам Яндекса" />
              <Kpi label="Клики" value={count(totals.clicks)} hint={`CTR ${percent(ratios.ctr_bp)}`} />
              <Kpi label="Обращения" value={count(totals.conversions)} hint={`конверсия ${percent(ratios.conversion_bp)}`} />
              <Kpi label="Цена обращения" value={rub(ratios.cpa)} hint={`клик ${rubExact(ratios.cpc)}`} />
              <Kpi label="Клиенты" value="—" hint="подтвердите в пятницу" muted />
            </dl>
            <DailyChart days={overview.days} metric="cost" />
          </section>

          <section className="cab-panel" aria-labelledby="dirs">
            <div className="cab-panel-head">
              <h2 id="dirs">Направления</h2>
              <button type="button" className="cab-link" onClick={() => go("campaigns")}>Все показатели</button>
            </div>
            <CampaignTable campaigns={overview.campaigns} compact />
          </section>
        </div>

        <aside className="cab-col">
          <section className="cab-panel" aria-labelledby="changes">
            <div className="cab-panel-head">
              <h2 id="changes">Что изменилось</h2>
              <button type="button" className="cab-link" onClick={() => go("history")}>Вся история</button>
            </div>
            {recent === null && <p className="cab-empty">Загружаем…</p>}
            {recent?.length === 0 && <p className="cab-empty">Действий пока не было.</p>}
            <ol className="cab-feed">
              {recent?.map((line) => (
                <li key={line.ref}>
                  <span className="cab-muted">{dateTime(line.at)}</span>
                  <span>{describeChange(line, overview).title}</span>
                  {describeChange(line, overview).detail.map((d) => <span key={d} className="cab-diff">{d}</span>)}
                </li>
              ))}
            </ol>
          </section>
          {money ? <MoneyPanel money={money} /> : (
            <section className="cab-panel"><h2>Деньги</h2><p className="cab-empty">Бюджет на этот месяц ещё не задан.</p></section>
          )}
        </aside>
      </div>
    </>
  );
}

function Kpi({ label, value, hint, muted }: { label: string; value: string; hint: string; muted?: boolean }) {
  return (
    <div className={muted ? "cab-kpi cab-kpi-muted" : "cab-kpi"}>
      <dt>{label}</dt>
      <dd>{value}</dd>
      <dd className="cab-muted">{hint}</dd>
    </div>
  );
}

function MoneyPanel({ money }: { money: Money }) {
  const { period } = money;
  const spent = period.spent ?? 0;
  const used = Math.min(100, (spent * 100) / period.hard_limit);
  const time = money.elapsed_bp / 100;
  const reserve = period.hard_limit - money.spendable;
  const left = period.opening_balance !== null && period.last_balance !== null ? period.opening_balance - period.last_balance : null;
  const statSpent = period.spent !== null ? period.spent - (period.opening_spent ?? 0) : null;
  const gap = money.balance_gap;
  const ok = gap !== null && gap <= money.balance_tolerance;

  return (
    <section className="cab-panel" aria-labelledby="money">
      <h2 id="money">Деньги за {moscowDay(period.starts_at)} — {moscowDay(new Date(new Date(period.ends_at).getTime() - 1).toISOString())}</h2>
      <p className="cab-money-line">
        Потрачено <strong>{rub(spent)}</strong> из {rub(period.hard_limit)}
      </p>
      <div className="cab-meter" role="img" aria-label={`Потрачено ${Math.round(used)}% бюджета, прошло ${Math.round(time)}% месяца`}>
        <span className="cab-meter-fill" style={{ width: `${used}%` }} />
        <span className="cab-meter-time" style={{ left: `${time}%` }} title="Сегодня" />
      </div>
      <p className="cab-muted">Прошло {Math.round(time)}% месяца, потрачено {Math.round(used)}% бюджета.</p>
      <table className="cab-kv">
        <tbody>
          <tr><th>Прогноз к концу месяца</th><td>{money.forecast === null ? "мало данных" : rub(money.forecast)}</td></tr>
          <tr><th>Защита бюджета</th><td>лимиты в Директе не выше {rub(money.spendable)}</td></tr>
          <tr><th>Запас на задержку статистики</th><td>{rub(reserve)}</td></tr>
        </tbody>
      </table>

      <h3 className="cab-sub">Сверка со счётом</h3>
      {gap === null || left === null || statSpent === null ? (
        <p className="cab-empty">Баланс счёта ещё не прочитан: сверка начнётся при следующей проверке.</p>
      ) : (
        <>
          <table className="cab-kv">
            <tbody>
              <tr><th>Баланс в начале сверки</th><td>{rub(period.opening_balance)}</td></tr>
              <tr><th>Баланс сейчас</th><td>{rub(period.last_balance)}</td></tr>
              <tr><th>Ушло со счёта</th><td>{rub(left)}</td></tr>
              <tr><th>Расход по статистике</th><td>{rub(statSpent)}</td></tr>
              <tr className={ok ? "cab-row-ok" : "cab-row-bad"}>
                <th>Разница</th>
                <td>{rub(gap)} <span className="cab-muted">(допуск {rub(money.balance_tolerance)})</span></td>
              </tr>
            </tbody>
          </table>
          <p className={ok ? "cab-ok" : "cab-bad"}>
            {ok ? "✓ Сходится" : "✕ Не сходится"} · проверено {dateTime(period.balance_checked_at)}
          </p>
        </>
      )}
    </section>
  );
}

function AlertCard({ alert, money, api, onChanged, go }: { alert: Alert; money?: Money; api: Api; onChanged: () => void; go: (s: Section) => void }) {
  const [busy, setBusy] = useState(false);
  const [asked, setAsked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const text = ALERT[alert.kind] ?? { title: "Нужна ваша проверка", tone: "warn" as const };

  const confirm = async () => {
    if (!money) return;
    setBusy(true);
    setError(null);
    try {
      await api.confirmBalance(money.period.account);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`cab-alert cab-alert-${text.tone}`} role="alert">
      <h2 className="cab-alert-title">{text.title}</h2>
      <p className="cab-muted">С {dateTime(alert.created_at)}</p>
      {alert.kind === "balance_mismatch" && money && money.balance_gap !== null ? (
        <>
          <p>
            С начала сверки со счёта ушло на <strong>{rub(money.balance_gap)}</strong> больше, чем реклама потратила по статистике Яндекса.
            Это больше допуска {rub(money.balance_tolerance)}, поэтому мы <strong>остановили показы</strong>: пока не ясно, куда уходят деньги, тратить их дальше нельзя.
          </p>
          <p className="cab-muted">
            Так бывает, если в этом кабинете идёт другая реклама не через Ad Helper, вы переводили деньги в другой кабинет или Яндекс задержал статистику.
          </p>
          {!asked ? (
            <div className="cab-actions">
              <button type="button" className="cab-btn cab-btn-primary" onClick={() => setAsked(true)}>Я знаю причину</button>
              <button type="button" className="cab-btn" onClick={() => go("history")}>Что мы сделали</button>
            </div>
          ) : (
            <div className="cab-confirm">
              <p>
                Сверка начнётся заново от нынешнего баланса {rub(money.period.last_balance)}. Показы сами не включатся: это отдельное действие, его запишем в историю.
              </p>
              <div className="cab-actions">
                <button type="button" className="cab-btn cab-btn-primary" disabled={busy} onClick={confirm}>Да, всё верно</button>
                <button type="button" className="cab-btn" onClick={() => setAsked(false)}>Не сейчас</button>
              </div>
            </div>
          )}
          {error && <p className="cab-bad">{error}</p>}
        </>
      ) : (
        <p>Подробности и решение — в истории действий.</p>
      )}
    </div>
  );
}
