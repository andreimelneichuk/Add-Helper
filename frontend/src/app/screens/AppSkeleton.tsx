import "@fontsource-variable/onest";
import "../tokens.css";
import "../base.css";
import { BrandLogo, Icon } from "../icons";
import type { View } from "../data";

const SKELETON_VIEWS: [View, string][] = [
  ["overview", "Обзор"],
  ["stats", "Статистика"],
  ["ads", "Реклама"],
  ["history", "История"],
  ["settings", "Настройки"],
];

export function AppSkeleton() {
  return (
    <div className="ah" role="status" aria-label="Загружаем данные…">
      <header className="ah-bar">
        <div className="ah-bar-site">
          <div className="ah-brand-selector">
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <BrandLogo size={22} />
              <span className="ah-skeleton-shimmer" style={{ width: 140, height: 18, display: "inline-block" }} />
            </span>
          </div>
        </div>
        <span className="ah-status" style={{ background: "var(--panel)", color: "var(--muted)", border: "1px solid var(--line)" }}>
          <span className="ah-skeleton-shimmer" style={{ width: 64, height: 12, display: "inline-block" }} />
        </span>
      </header>
      <div className="ah-body">
        <nav className="ah-rail" aria-label="Разделы">
          {SKELETON_VIEWS.map(([id, label], i) => (
            <button key={id} type="button" aria-current={i === 0 ? "page" : undefined} disabled tabIndex={-1} style={{ cursor: "default" }}>
              <Icon name={id} />
              {label}
            </button>
          ))}
          <button type="button" disabled tabIndex={-1} style={{ cursor: "default" }}>
            <Icon name="chat" />
            Чат с ИИ
          </button>
        </nav>
        <main className="ah-main">
          <div className="ov">
            {/* Карточка внимания / статус */}
            <div className="ah-skeleton-shimmer" style={{ height: 76, borderRadius: "var(--r-surface)" }} />

            {/* Сетка ключевых показателей */}
            <div className="ov-kpis">
              {[0, 1, 2, 3].map((idx) => (
                <div key={idx} className="ah-panel" style={{ padding: "var(--pad)", display: "flex", flexDirection: "column", gap: 10 }}>
                  <div className="ah-skeleton-shimmer" style={{ width: "55%", height: 12 }} />
                  <div className="ah-skeleton-shimmer" style={{ width: "80%", height: 26 }} />
                  <div className="ah-skeleton-shimmer" style={{ width: "40%", height: 10 }} />
                </div>
              ))}
            </div>

            {/* Ряд с графиком и лентой */}
            <div className="ov-row">
              <div className="ah-panel" style={{ padding: "var(--pad)", minHeight: 220, display: "flex", flexDirection: "column", gap: 14 }}>
                <div className="ah-skeleton-shimmer" style={{ width: 180, height: 18 }} />
                <div className="ah-skeleton-shimmer" style={{ flex: 1, minHeight: 140 }} />
              </div>
              <div className="ah-panel" style={{ padding: "var(--pad)", minHeight: 220, display: "flex", flexDirection: "column", gap: 14 }}>
                <div className="ah-skeleton-shimmer" style={{ width: 140, height: 18 }} />
                <div className="ah-skeleton-shimmer" style={{ flex: 1, minHeight: 140 }} />
              </div>
            </div>

            {/* Ряд с таблицей объявлений и историей */}
            <div className="ov-row">
              <div className="ah-panel" style={{ padding: "var(--pad)", minHeight: 180, display: "flex", flexDirection: "column", gap: 14 }}>
                <div className="ah-skeleton-shimmer" style={{ width: 200, height: 18 }} />
                <div className="ah-skeleton-shimmer" style={{ height: 38 }} />
                <div className="ah-skeleton-shimmer" style={{ height: 38 }} />
              </div>
              <div className="ah-panel" style={{ padding: "var(--pad)", minHeight: 180, display: "flex", flexDirection: "column", gap: 14 }}>
                <div className="ah-skeleton-shimmer" style={{ width: 160, height: 18 }} />
                <div className="ah-skeleton-shimmer" style={{ height: 30 }} />
                <div className="ah-skeleton-shimmer" style={{ height: 30 }} />
              </div>
            </div>
          </div>
        </main>
      </div>
      <nav className="ah-nav" aria-label="Разделы на телефоне">
        {SKELETON_VIEWS.map(([id, label], i) => (
          <button key={id} type="button" aria-current={i === 0 ? "page" : undefined} disabled tabIndex={-1} style={{ cursor: "default" }}>
            <Icon name={id} />
            {label}
          </button>
        ))}
        <button type="button" disabled tabIndex={-1} style={{ cursor: "default" }}>
          <Icon name="chat" />
          Чат
        </button>
      </nav>
    </div>
  );
}
