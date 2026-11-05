// Объявления, которые сервис написал сам и не запускает без владельца, и что готовится к запуску.
// Решение не трогает площадку: объявление лишь переходит в очередь на отправку, поэтому идёт прямо в API.
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { Api, Board } from "../../api";
import { CREATIVE_NEXT, STATE_REASON, WRITE_REASON } from "../words";
import type { LiveStore } from "../live";

// Пока пишутся тексты, перечитываем: набор готов через минуту-другую.
const POLL_MS = 10_000;

type Item = Board["creatives"][number];

const campaignOf = (c: { campaign_name: string | null; campaign_id: string }) => c.campaign_name ?? `Направление № ${c.campaign_id}`;
const message = (e: unknown) => (e instanceof Error ? e.message : "Сервис не ответил. Ничего не изменилось, попробуйте позже.");

function plural(n: number, one: string, few: string, many: string) {
  const tail = n % 100;
  if (tail >= 11 && tail <= 14) return many;
  if (n % 10 === 1) return one;
  return n % 10 >= 2 && n % 10 <= 4 ? few : many;
}

/** Одна фраза о том, что сервис делает с объявлениями сейчас. */
function now(board: Board): string | null {
  const pending = board.creatives.filter((c) => c.state === "launch_pending").length;
  const reserve = board.creatives.filter((c) => c.state === "ready" || c.state === "reserve").length;
  const parts: string[] = [];
  if (pending) parts.push(`${pending} ${plural(pending, "объявление готовлю", "объявления готовлю", "объявлений готовлю")} к запуску`);
  if (reserve) parts.push(`${reserve} в запасе на замену`);
  for (const w of board.writing) parts.push(`пишу новые ${WRITE_REASON[w.reason] ?? ""} «${campaignOf(w)}»`.replace("  ", " "));
  if (!parts.length) return null;
  const text = parts.join(", ");
  return text[0]!.toUpperCase() + text.slice(1) + ".";
}

const QUEUE = ["launch_pending", "ready", "reserve"];
const QUEUE_WORD: Record<string, string> = { launch_pending: "уйдёт в Яндекс первым", ready: "ждёт места", reserve: "в запасе на замену" };

function Queue({ items, busy, act }: { items: Item[]; busy: boolean; act: (item: Item, launch: boolean) => void }) {
  return (
    <section className="ah-panel ads-queue" aria-labelledby="ads-queue-title">
      <h2 id="ads-queue-title" className="ah-panel-title">Готовлю к запуску</h2>
      <p className="ah-muted">Эти объявления я написал сам по фактам профиля. Если какое-то вам не нравится, снимите его, и я напишу другое.</p>
      <ul className="ads-queue-list">
        {items.map((c) => (
          <li key={c.id}>
            <div><strong>{c.headline}</strong><div className="ads-facts">{campaignOf(c)}, {QUEUE_WORD[c.state]}</div></div>
            <button type="button" className="ah-btn" disabled={busy} aria-label={`Не запускать «${c.headline}»`} onClick={() => act(c, false)}>Не запускать</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Decision({ item, busy, act }: { item: Item; busy: boolean; act: (item: Item, launch: boolean) => void }) {
  const facts = [...new Set(item.sources.map((x) => x.label))].join(", ");
  return (
    <article className="ads-card" aria-label={`Решение по «${item.headline}»`}>
      <div className="ads-body">
        <div className="ah-label">{campaignOf(item)}</div>
        <div className="ads-title">{item.headline}</div>
        <div className="ads-text">{item.text}</div>
        <div className="ads-facts">{facts ? `Факты из профиля: ${facts}` : "В тексте нет фактов из профиля"}</div>
        {STATE_REASON[item.state_reason] && <div className="ads-facts">{STATE_REASON[item.state_reason]}</div>}
        <div className="ads-foot">
          <button type="button" className="ah-btn ah-btn-primary" disabled={busy} onClick={() => act(item, true)}>Запустить</button>
          <button type="button" className="ah-btn" disabled={busy} onClick={() => act(item, false)}>Не запускать</button>
        </div>
      </div>
    </article>
  );
}

/** Решения владельца первыми, затем список объявлений экрана, затем очередь на запуск. */
export function Decisions({ api, store, children }: { api: Api; store: LiveStore; children: ReactNode }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.creatives().then(setBoard, (e: unknown) => setError(message(e))), [api]);
  useEffect(() => { void load(); }, [load]);
  const writing = (board?.writing.length ?? 0) > 0;
  useEffect(() => {
    if (!writing) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [writing, load]);

  const act = async (item: Item, launch: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await (launch ? api.launch(item.id) : api.withdraw(item.id));
      store.set({ notice: launch
        ? `Разрешил запуск «${item.headline}». ${CREATIVE_NEXT.launch_pending} Передумать можно ниже, в списке «Готовлю к запуску», пока оно не ушло в Яндекс.`
        : `Не запускаю «${item.headline}». ${CREATIVE_NEXT.withdrawn}` });
      // Тревога «нужно решение» снимается сервисом; перечитываем, чтобы она ушла и с обзора.
      await Promise.all([load(), store.refresh()]);
    } catch (e) {
      setError(message(e));
      await load();
    } finally {
      setBusy(false);
    }
  };

  if (!board) return <>{children}{error && <p className="ah-error" role="alert">{error}</p>}</>;
  const decide = board.creatives.filter((c) => c.state === "needs_owner");
  const queue = board.creatives.filter((c) => QUEUE.includes(c.state)).sort((a, b) => QUEUE.indexOf(a.state) - QUEUE.indexOf(b.state));
  const line = now(board);
  return (
    <>
      {decide.length > 0 && (
        <section className="ah-panel ads-decide" aria-labelledby="ads-decide-title">
          <h2 id="ads-decide-title" className="ah-panel-title">Нужно ваше решение</h2>
          <p className="ah-muted">{CREATIVE_NEXT.needs_owner}</p>
          <div className="ads-cards">
            {decide.map((c) => <Decision key={c.id} item={c} busy={busy} act={act} />)}
          </div>
        </section>
      )}
      {error && <p className="ah-error" role="alert">{error}</p>}
      {children}
      {line && <p className="ah-muted" role="status" aria-label="Что с объявлениями сейчас">{line}</p>}
      {queue.length > 0 && <Queue items={queue} busy={busy} act={act} />}
    </>
  );
}
