import { useState, useEffect } from "react";
import { getToken, checkAuth } from "./api";
import LoginPage from "./pages/LoginPage";
import MainPage from "./pages/MainPage";
import "./styles/global.css";

export default function App() {
  const [authorized, setAuthorized] = useState<boolean | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setAuthorized(false);
      return;
    }
    checkAuth()
      .then(() => setAuthorized(true))
      .catch(() => setAuthorized(false));
  }, []);

  if (authorized === null) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", color: "var(--text-secondary)" }}>
        <span className="spinner" />
      </div>
    );
  }

  if (!authorized) {
    return <LoginPage onLogin={() => setAuthorized(true)} />;
  }

  return <MainPage />;
}
