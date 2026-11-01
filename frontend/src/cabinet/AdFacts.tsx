import type { Audience, Basis } from "../api";
import { PLACEMENT } from "./words";

// Кому показываем объявление: где, в каком регионе, по каким запросам и когда.
export function AudiencePanel({ audience }: { audience: Audience }) {
  const empty = !audience.placement && audience.regions.length === 0 && audience.phrases.length === 0 && !audience.schedule;
  return (
    <section className="cab-panel" aria-labelledby="audience">
      <h2 id="audience">Кому показываем</h2>
      {empty ? (
        <p className="cab-muted">Яндекс пока не передал настройки показа этого направления.</p>
      ) : (
        <dl className="cab-audience">
          {audience.placement && (
            <div>
              <dt>Где</dt>
              <dd>{PLACEMENT[audience.placement] ?? audience.placement}</dd>
            </div>
          )}
          {audience.regions.length > 0 && (
            <div>
              <dt>Кому</dt>
              <dd>Тем, кто в регионе: {audience.regions.join(", ")}</dd>
            </div>
          )}
          {audience.phrases.length > 0 && (
            <div>
              <dt>Когда ищут</dt>
              <dd>
                <ul className="cab-phrases">
                  {audience.phrases.map((p) => <li key={p}>{p}</li>)}
                </ul>
              </dd>
            </div>
          )}
          {audience.schedule && (
            <div>
              <dt>Время</dt>
              <dd>{audience.schedule}</dd>
            </div>
          )}
        </dl>
      )}
    </section>
  );
}

// На какие поля профиля опирается текст. Число не из профиля — повод проверить профиль.
export function BasisPanel({ basis, live }: { basis: Basis; live: boolean }) {
  return (
    <section className="cab-panel" aria-labelledby="basis">
      <h2 id="basis">Откуда факты</h2>
      {basis.unconfirmed.length > 0 ? (
        <p className="cab-alert cab-alert-warn">
          {basis.unconfirmed.length === 1 ? "Числа" : "Чисел"} {basis.unconfirmed.join(", ")} нет в вашем профиле.{" "}
          {live
            ? "Если цена изменилась, обновите профиль: замену напишем сами."
            : "Такое объявление не запустим: заменим текстом с актуальными данными."}
        </p>
      ) : basis.sources.length > 0 ? (
        <p className="cab-ok">✓ Все факты — из вашего профиля</p>
      ) : (
        <p className="cab-muted">В тексте нет цен, адреса и других фактов, которые надо подтверждать.</p>
      )}
      {basis.sources.length > 0 && (
        <ul className="cab-basis">
          {basis.sources.map((s) => (
            <li key={s.key}>
              <mark>{s.fragment}</mark>
              <span className="cab-muted">{s.label}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
