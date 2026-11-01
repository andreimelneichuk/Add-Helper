import { useCallback, useEffect, useState } from "react";
import type { Api, CreativeSet, Profile } from "../api";
import { dateTime } from "../format";
import { REJECTION, SET_NOTE } from "./words";

const WAITING = ["queued", "running"];

function domain(website: string): string {
  try {
    return new URL(website).hostname;
  } catch {
    return website || "ваш-сайт";
  }
}

export function Ads({ api }: { api: Api }) {
  const [sets, setSets] = useState<CreativeSet[] | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => api.sets().then(setSets, (e: Error) => setMessage(e.message)), [api]);
  useEffect(() => {
    load();
    api.profile().then(setProfile, () => setProfile(null));
  }, [api, load]);

  // Пока набор в очереди, спрашиваем раз в 3 секунды: генерация идёт в воркере.
  const waiting = sets?.some((s) => WAITING.includes(s.status)) ?? false;
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [waiting, load]);

  const request = async () => {
    setMessage(null);
    try {
      await api.requestSet();
      await load();
    } catch (e) {
      setMessage((e as Error).message);
    }
  };

  const decide = async (set: CreativeSet, index: number, decision: "approved" | "declined") => {
    await api.decide(set.id, index, decision);
    await load();
  };

  return (
    <>
      <div className="cab-head cab-head-row">
        <div>
          <h1>Объявления</h1>
          <p className="cab-muted cab-period">Пишем только то, что есть в профиле: цены, услуги, адрес, особенности. Каждое объявление проверяет код, а смысл — отдельная модель.</p>
        </div>
        <button type="button" className="cab-btn cab-btn-primary" onClick={request} disabled={waiting}>
          {waiting ? "Пишем объявления…" : "Сделать новые объявления"}
        </button>
      </div>
      {message && <p className="cab-alert cab-alert-warn" role="alert">{message}</p>}
      {sets === null && <p className="cab-empty">Загружаем…</p>}
      {sets?.length === 0 && <p className="cab-empty">Наборов пока нет. Нажмите «Сделать новые объявления».</p>}
      {sets?.map((set) => (
        <section key={set.id} className="cab-panel">
          <div className="cab-panel-head">
            <h2>Набор от {dateTime(set.created_at)}</h2>
            <span className="cab-muted">{setStatus(set)}</span>
          </div>
          {set.notes.map((note) => <p key={note} className="cab-muted">{SET_NOTE[note] ?? note}</p>)}
          {set.needs_review && <p className="cab-alert cab-alert-warn">Чувствительная ниша: объявления запустим только после вашего подтверждения.</p>}
          <div className="cab-ads">
            {set.drafts.map((draft, index) => (
              <article key={index} className={`cab-ad cab-ad-${draft.decision}`}>
                <div className="cab-ad-preview" aria-label="Так объявление выглядит в поиске">
                  <span className="cab-ad-mark">Реклама</span>
                  <a className="cab-ad-title" href={profile?.website || undefined} onClick={(e) => e.preventDefault()}>{draft.headline}</a>
                  <span className="cab-ad-url">{domain(profile?.website ?? "")}</span>
                  <p className="cab-ad-text">{draft.text}</p>
                </div>
                <div className="cab-ad-actions">
                  {draft.decision === "proposed" ? (
                    <>
                      <button type="button" className="cab-btn cab-btn-primary" onClick={() => decide(set, index, "approved")}>Одобрить</button>
                      <button type="button" className="cab-btn" onClick={() => decide(set, index, "declined")}>Отклонить</button>
                    </>
                  ) : (
                    <span className={draft.decision === "approved" ? "cab-ok" : "cab-muted"}>
                      {draft.decision === "approved" ? "✓ Одобрено вами" : "Отклонено вами"}
                    </span>
                  )}
                  <span className="cab-muted cab-len">{draft.headline.length}/56 · {draft.text.length}/81</span>
                </div>
              </article>
            ))}
          </div>
          {set.rejected.length > 0 && (
            <details className="cab-rejected">
              <summary>Снято проверкой: {set.rejected.length}</summary>
              <ul>
                {set.rejected.map((r, i) => (
                  <li key={i}>
                    <strong>{r.headline}</strong> — {r.text}
                    <br />
                    <span className="cab-bad">{r.reasons.map((reason) => REJECTION[reason] ?? reason).join("; ")}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      ))}
    </>
  );
}

function setStatus(set: CreativeSet): string {
  if (set.status === "queued") return "В очереди";
  if (set.status === "running") return "Пишем…";
  if (set.status === "failed") return "Не получилось";
  const who = set.source === "model" ? "Написала модель, проверили код и модель-судья" : "Собрано по шаблонам из профиля";
  return `${who} · правила ${set.rules_version}`;
}
