import { useState } from "react";
import { login } from "../api";

interface Props {
  onLogin: () => void;
}

export default function LoginPage({ onLogin }: Props) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!password) return;
    setLoading(true);
    setError("");
    try {
      await login(password);
      onLogin();
    } catch (err: any) {
      setError(err.message || "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="login-card__title">即梦视频队列</div>
        <div className="login-card__subtitle">请输入密码</div>
        <input
          className="login-card__input"
          type="password"
          placeholder="密码"
          value={password}
          onChange={e => setPassword(e.target.value)}
          autoFocus
        />
        <button className="login-card__btn" type="submit" disabled={loading}>
          {loading ? "登录中..." : "登录"}
        </button>
        {error && <div className="login-card__error">{error}</div>}
      </form>
    </div>
  );
}
