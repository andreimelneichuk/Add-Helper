import { BrandLogo } from "../icons";
import "./Login.css";

export function Login({ error, onLogin }: { error?: string | null; onLogin?: () => void }) {
  const handleLogin = () => {
    if (onLogin) {
      onLogin();
      return;
    }
    try {
      document.cookie = "ah_guest=; path=/; max-age=0";
    } catch {
      // ignore in environments without cookie access
    }
    window.location.assign("/api/auth/yandex/login");
  };

  return (
    <div className="ah-login-wrap">
      <main className="ah-panel ah-login-card">
        <div className="ah-login-brand">
          <BrandLogo size={48} />
          <h1 className="ah-login-title">Ad Helper</h1>
        </div>
        <p className="ah-login-subtitle">
          Управление контекстной рекламой без сложных настроек
        </p>

        {error && (
          <div role="alert" className="ah-login-alert">
            {error}
          </div>
        )}

        <div className="ah-login-actions">
          <button
            type="button"
            className="ah-btn ah-btn-primary ah-login-btn"
            onClick={handleLogin}
          >
            Войти через Яндекс
          </button>
        </div>
      </main>
    </div>
  );
}
