import { useEffect, useState } from "react";
import type { Api, LaunchAnswer, LaunchCheck } from "../../../api";
import { liveApi } from "../../live";
import type { Store } from "../../store";

type LaunchModalProps = {
  isOpen: boolean;
  onClose: () => void;
  store: Store;
  api?: Pick<Api, "launchCheck" | "startLaunch"> | null;
};

export function LaunchModal({ isOpen, onClose, store, api: propsApi }: LaunchModalProps) {
  const api = propsApi ?? liveApi(store) ?? (store as any).api;

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<LaunchCheck | null>(null);
  const [answer, setAnswer] = useState<LaunchAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadCheck = () => {
    if (!api) return;
    setLoading(true);
    setError(null);
    api
      .launchCheck()
      .then((res: LaunchCheck) => {
        setCheck(res);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Сервис не ответил. Попробуйте позже.");
        setLoading(false);
      });
  };

  useEffect(() => {
    if (isOpen) {
      setAnswer(null);
      loadCheck();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleLaunch = async () => {
    if (!api || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res: LaunchAnswer = await api.startLaunch();
      setAnswer(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Не удалось запустить рекламу. Попробуйте позже.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ah-modal-overlay" role="dialog" aria-modal="true" aria-labelledby="launch-modal-title">
      <div className="ah-modal-card" style={{ maxWidth: 540 }}>
        <div className="ah-modal-head">
          <h2 id="launch-modal-title" style={{ margin: 0, fontSize: "18px", fontWeight: 700 }}>
            {answer ? "Реклама запущена" : "Запуск рекламы"}
          </h2>
          <button
            type="button"
            className="ah-modal-close"
            onClick={answer ? () => window.location.reload() : onClose}
            aria-label="Закрыть"
            disabled={busy}
          >
            ×
          </button>
        </div>

        {loading && (
          <div style={{ padding: "32px 0", textAlign: "center" }}>
            <p className="ah-muted" role="status">Проверяю готовность к запуску рекламы…</p>
          </div>
        )}

        {!loading && answer && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ background: "var(--accent-soft)", padding: "16px", borderRadius: "12px", border: "1px solid var(--accent)" }}>
              <strong style={{ display: "block", color: "var(--ink)", marginBottom: 6 }}>
                Кампании созданы в Яндекс Директе
              </strong>
              <p style={{ margin: 0, fontSize: "14px", color: "var(--ink)" }}>
                {answer.text || "Объявления отправлены на модерацию в Яндекс Директ. Показы начнутся сразу после проверки."}
              </p>
            </div>

            {answer.campaigns && answer.campaigns.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <span className="ah-label">Созданные кампании</span>
                {answer.campaigns.map((c, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      padding: "10px 14px",
                      background: "var(--canvas)",
                      border: "1px solid var(--line)",
                      borderRadius: "10px",
                      fontSize: "14px",
                    }}
                  >
                    <span>{c.name}</span>
                    <strong>{c.limit_rubles} ₽/нед</strong>
                  </div>
                ))}
              </div>
            )}

            <div className="ah-modal-foot">
              <button
                type="button"
                className="ah-btn ah-btn-primary"
                onClick={() => window.location.reload()}
                style={{ width: "100%" }}
              >
                Перейти в кабинет
              </button>
            </div>
          </div>
        )}

        {!loading && !answer && check && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span className="ah-label">Проверка готовности к старту</span>
              {check.items.map((item, idx) => (
                <div
                  key={idx}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 10,
                    padding: "10px 14px",
                    background: item.ok ? "var(--canvas)" : "var(--warn-soft, #fff8e6)",
                    border: `1px solid ${item.ok ? "var(--line)" : "var(--warn, #f5a623)"}`,
                    borderRadius: "10px",
                    fontSize: "14px",
                  }}
                >
                  <span style={{ color: item.ok ? "var(--accent)" : "var(--warn, #d98200)", fontWeight: 700, fontSize: "16px" }}>
                    {item.ok ? "✓" : "!"}
                  </span>
                  <span style={{ color: "var(--ink)", flex: 1 }}>{item.text}</span>
                </div>
              ))}
            </div>

            {check.launch_text && (
              <p className="ah-muted" style={{ margin: 0, fontSize: "13px" }}>
                {check.launch_text}
              </p>
            )}

            {error && <p className="ah-error" role="alert" style={{ margin: 0 }}>{error}</p>}

            <div className="ah-modal-foot">
              {!check.ready ? (
                <>
                  <button
                    type="button"
                    className="ah-btn"
                    onClick={() => {
                      onClose();
                      store.goView("settings");
                    }}
                  >
                    Перейти в настройки
                  </button>
                  <button type="button" className="ah-btn" onClick={loadCheck}>
                    Проверить снова
                  </button>
                </>
              ) : (
                <>
                  <button type="button" className="ah-btn" onClick={onClose} disabled={busy}>
                    Отмена
                  </button>
                  <button
                    type="button"
                    className="ah-btn ah-btn-primary"
                    onClick={handleLaunch}
                    disabled={busy || !check.launch_available}
                  >
                    {busy ? "Запускаю рекламу…" : "Запустить рекламу"}
                  </button>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
