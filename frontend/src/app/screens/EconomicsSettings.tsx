import { useCallback, useEffect, useState } from "react";
import type { Api, EconomicsPatch, EconomicsView } from "../../api";

// Слова владельца: «прибыль с чека», «клиентами становятся», без «маржи» и «конверсии».
type Draft = { check: string; lead: string; margin: string; repeat: string; share: string };

const pct = (bp: number) => String(bp / 100).replace(".", ",");
const num = (text: string) => Number(text.replace(",", ".").replace(/\s/g, ""));
const bp = (text: string) => Math.round(num(text) * 100);

function draftOf(view: EconomicsView): Draft {
  return {
    check: view.check_rubles === null ? "" : String(view.check_rubles),
    lead: pct(view.lead_to_client_bp),
    margin: pct(view.margin_bp),
    repeat: pct(view.repeat_bp),
    share: pct(view.ad_share_bp),
  };
}

function patchOf(draft: Draft, view: EconomicsView): EconomicsPatch | string {
  const patch: EconomicsPatch = {
    lead_to_client_bp: bp(draft.lead),
    margin_bp: bp(draft.margin),
    repeat_bp: bp(draft.repeat),
    ad_share_bp: bp(draft.share),
  };
  const values = [patch.lead_to_client_bp, patch.margin_bp, patch.repeat_bp, patch.ad_share_bp];
  if (values.some((v) => !Number.isFinite(v))) return "Впишите числа в процентах, например 30";
  if (draft.check.trim()) {
    const check = num(draft.check);
    if (!Number.isInteger(check) || check <= 0) return "Средний чек — целое число рублей больше нуля";
    if (check !== view.check_rubles || !view.check_from_profile) patch.avg_check_rubles = check;
  }
  return patch;
}

function problem(error: unknown): string {
  return error instanceof Error ? error.message : "Не получилось, попробуйте ещё раз";
}

export function EconomicsSettings({ api }: { api: Api }) {
  const [view, setView] = useState<EconomicsView | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const show = (next: EconomicsView) => { setView(next); setDraft(draftOf(next)); };
  const load = useCallback(async () => {
    try {
      const next = await api.economics();
      setView(next);
      // Позднее чтение не затирает то, что владелец уже начал вписывать.
      setDraft((current) => current ?? draftOf(next));
      setError("");
    } catch (e) { setError(problem(e)); }
  }, [api]);
  useEffect(() => { void load(); }, [load]);

  if (!view || !draft) return <p className="set-hint" aria-live="polite">{error || "Загружаю…"}</p>;
  const field = (key: keyof Draft, label: string, hint: string) => (
    <label className="eco-field">
      <span className="ah-label">{label}</span>
      <input className="ah-input" inputMode="decimal" aria-label={label} value={draft[key]} disabled={busy} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
      <span className="set-hint">{hint}</span>
    </label>
  );
  const save = async (confirm: boolean) => {
    const patch = patchOf(draft, view);
    if (typeof patch === "string") return setError(patch);
    setBusy(true);
    setError("");
    try { show(await api.saveEconomics({ ...patch, confirm })); } catch (e) { setError(problem(e)); } finally { setBusy(false); }
  };
  return (
    <div className="eco">
      <p className="eco-state" data-confirmed={view.confirmed}>
        {view.confirmed ? "Цифры подтверждены вами" : "Это ориентир для салона, не ваши цифры. Пока вы их не подтвердили, автопилот только предлагает."}
      </p>
      {view.goal_rubles === null
        ? <p className="set-hint">Укажите средний чек или цены услуг в профиле: тогда я посчитаю, сколько можно платить за обращение.</p>
        : <p className="eco-goal"><strong>Можно платить за обращение до {view.goal_rubles} ₽</strong>{view.confirmed ? "" : " (ориентир)"}</p>}
      {view.how && <p className="set-hint">Как посчитано: {view.how}</p>}
      <div className="eco-fields">
        {field("check", "Средний чек, ₽", view.check_from_profile ? "Посчитан по ценам услуг из профиля, можно поправить" : "Сколько клиент платит за визит")}
        {field("lead", "Из обращений клиентами становятся, %", "Из 10 обращений сколько приходят, например 3 из 10 — это 30")}
        {field("margin", "Прибыль с чека, %", "Что остаётся после расходов на услугу")}
        {field("repeat", "Повторные визиты, %", "На сколько процентов клиент приносит больше первого визита")}
        {field("share", "Сколько прибыли готовы тратить на рекламу, %", "Доля прибыли с клиента, которую вы отдаёте за его привлечение")}
      </div>
      {view.services.length > 0 && (
        <ul className="eco-services" aria-label="Допустимая цена обращения по услугам">
          {view.services.map((s) => <li key={s.service}>{s.service} ({s.price_rubles} ₽): до {s.goal_rubles} ₽ за обращение</li>)}
        </ul>
      )}
      <div className="ap-stop">
        <button type="button" className="ah-btn" disabled={busy} onClick={() => void save(false)}>Сохранить цифры</button>
        <button type="button" className="ah-btn ah-btn-primary" disabled={busy} onClick={() => void save(true)}>Это мои цифры</button>
      </div>
      {error && <p className="ah-error" role="alert">{error}</p>}
    </div>
  );
}
