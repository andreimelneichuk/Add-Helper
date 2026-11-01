import { useCallback, useEffect, useState } from "react";
import type { Api, Channels } from "../api";
import { ALERT } from "./words";

// Пока владелец открывает бота, проверяем привязку сами: бот читает сообщения раз в минуту.
const WAIT_MS = 10_000;

export function NotificationsScreen({ api }: { api: Api }) {
  const [channels, setChannels] = useState<Channels | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.channels().then(setChannels, (e: Error) => setError(e.message)), [api]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!link || channels?.telegram.linked) return;
    const timer = window.setInterval(load, WAIT_MS);
    return () => window.clearInterval(timer);
  }, [link, channels, load]);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const connect = () =>
    act(async () => {
      const { url } = await api.linkTelegram();
      setLink(url);
      window.open(url, "_blank", "noopener");
    });
  const disconnect = () =>
    act(async () => {
      await api.unlinkTelegram();
      setLink(null);
      await load();
    });

  if (!channels) return <p className="cab-empty">{error ?? "Загружаем…"}</p>;
  const { telegram } = channels;

  return (
    <>
      <div className="cab-head">
        <h1>Уведомления</h1>
        <p className="cab-muted cab-period">Напишем, если показы остановлены или с деньгами что-то не так. Каждое сообщение говорит, что случилось, что мы сделали и что делать вам.</p>
      </div>
      <section className="cab-panel cab-form" aria-labelledby="channels">
        <h2 id="channels">Куда писать</h2>
        <div className="cab-channel">
          <div>
            <strong>Почта</strong>
            <p className="cab-muted">{channels.email ?? "В Яндекс ID нет почты: письма не придут. Добавьте почту в Яндекс ID и войдите заново."}</p>
          </div>
          <span className={channels.email ? "cab-ok" : "cab-bad"}>{channels.email ? "Включена" : "Нет адреса"}</span>
        </div>
        <div className="cab-channel">
          <div>
            <strong>Telegram</strong>
            <p className="cab-muted">
              {!telegram.available
                ? "Бот пока не настроен на сервере."
                : telegram.linked
                  ? "Тревоги приходят и в Telegram."
                  : link
                    ? "Откройте бота и нажмите «Запустить». Ссылка действует 15 минут, проверим подключение сами."
                    : "Тревоги придут сразу в телефон: письмо можно не заметить."}
            </p>
            {link && !telegram.linked && (
              <p><a className="cab-link" href={link} target="_blank" rel="noopener noreferrer">Открыть бота ещё раз</a></p>
            )}
          </div>
          {telegram.available &&
            (telegram.linked ? (
              <button type="button" className="cab-btn" disabled={busy} onClick={disconnect}>Отключить</button>
            ) : (
              <button type="button" className="cab-btn cab-btn-primary" disabled={busy} onClick={connect}>Подключить</button>
            ))}
        </div>
        {error && <p className="cab-bad">{error}</p>}
      </section>
      <section className="cab-panel cab-form" aria-labelledby="when">
        <h2 id="when">Когда напишем</h2>
        <ul className="cab-when">
          {Object.entries(ALERT).map(([kind, text]) => (
            <li key={kind}>{text.title}</li>
          ))}
        </ul>
        <p className="cab-muted">О каждой проблеме пишем один раз, пока она не решится. Если отправка не удалась, повторяем несколько раз в течение часа.</p>
      </section>
    </>
  );
}
