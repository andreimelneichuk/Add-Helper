import { useState } from "react";
import { LiveStore } from "../live";
import type { Store } from "../store";

type CreateSiteModalProps = {
  isOpen: boolean;
  onClose: () => void;
  store: Store;
};

export function CreateSiteModal({ isOpen, onClose, store }: CreateSiteModalProps) {
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const liveStore = store instanceof LiveStore ? store : null;

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("Укажите название бизнеса или сайта.");
      return;
    }

    setBusy(true);
    setError(null);
    try {
      if (liveStore && liveStore.createSite) {
        await liveStore.createSite(trimmedName, website.trim() || undefined);
        onClose();
      } else {
        window.location.assign("/?start=new");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать сайт. Попробуйте позже.");
    } finally {
      setBusy(false);
    }
  };

  const handleStartWizard = () => {
    onClose();
    if (liveStore) {
      liveStore.addNewSite();
    } else {
      window.location.assign("/?start=new");
    }
  };

  return (
    <div className="ah-modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="ah-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-site-modal-title"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 520 }}
      >
        <div className="ah-modal-head">
          <h2 id="new-site-modal-title" className="ah-modal-title">
            Добавить сайт или бизнес
          </h2>
          <button
            type="button"
            className="ah-modal-close"
            aria-label="Закрыть"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <p className="ah-muted" style={{ margin: 0, fontSize: 14 }}>
            Создайте отдельный проект с собственным бюджетом, профилем и кампаниями в Директе.
          </p>

          <label>
            <span className="ah-label">Название бизнеса или сайта</span>
            <input
              className="ah-input"
              style={{ width: "100%", marginTop: 4 }}
              placeholder="Например: Автосервис «Мотор» или salon-beauty.ru"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setError(null);
              }}
              autoFocus
              disabled={busy}
            />
          </label>

          <label>
            <span className="ah-label">Ссылка на сайт (опционально)</span>
            <input
              className="ah-input"
              style={{ width: "100%", marginTop: 4 }}
              placeholder="https://ваш-сайт.рф"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
              disabled={busy}
            />
          </label>

          {error && <p className="ah-error" role="alert" style={{ margin: 0 }}>{error}</p>}

          <div className="ah-modal-foot" style={{ marginTop: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <button
              type="button"
              className="ah-link"
              style={{ fontSize: 13 }}
              onClick={handleStartWizard}
              disabled={busy}
            >
              Пройти мастер запуска с нуля →
            </button>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className="ah-btn"
                onClick={onClose}
                disabled={busy}
              >
                Отмена
              </button>
              <button
                type="submit"
                className="ah-btn ah-btn-primary"
                disabled={busy || !name.trim()}
              >
                {busy ? "Создаю…" : "Создать сайт"}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
