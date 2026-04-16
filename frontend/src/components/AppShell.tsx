import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { api } from "../api";
import { useAuth } from "../hooks/useAuth";

type AppShellProps = {
  children: ReactNode;
};

export function AppShell({ children }: AppShellProps) {
  const { user, loading, isAuthenticated, setUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const handleLogout = async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
      navigate("/login");
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/dashboard">
          多智能体商业分析
        </Link>
        <div className="topbar-actions">
          {loading ? <span className="muted">正在检查登录状态...</span> : null}
          {!loading && isAuthenticated ? (
            <>
              <span className="user-chip">{user?.username}</span>
              <button className="ghost-button" onClick={handleLogout} type="button">
                退出登录
              </button>
            </>
          ) : null}
          {!loading && !isAuthenticated ? (
            <>
              {location.pathname !== "/login" ? (
                <Link className="ghost-button link-button" to="/login">
                  登录
                </Link>
              ) : null}
              {location.pathname !== "/signup" ? (
                <Link className="primary-button link-button" to="/signup">
                  注册
                </Link>
              ) : null}
            </>
          ) : null}
        </div>
      </header>
      <main className="page-shell">{children}</main>
    </div>
  );
}
