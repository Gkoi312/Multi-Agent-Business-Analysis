import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { AuthForm } from "../components/AuthForm";
import { useAuth } from "../hooks/useAuth";

export function LoginPage() {
  const navigate = useNavigate();
  const { setUser } = useAuth();

  return (
    <AuthForm
      title="登录"
      submitLabel="登录"
      onSubmit={async (payload) => {
        const user = await api.login(payload);
        setUser(user);
        navigate("/dashboard");
      }}
      footer={
        <p className="muted">
          还没有账号？<Link to="/signup">立即注册</Link>
        </p>
      }
    />
  );
}
