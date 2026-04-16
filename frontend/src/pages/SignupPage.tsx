import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { AuthForm } from "../components/AuthForm";
import { useAuth } from "../hooks/useAuth";

export function SignupPage() {
  const navigate = useNavigate();
  const { setUser } = useAuth();

  return (
    <AuthForm
      title="注册账号"
      submitLabel="注册"
      onSubmit={async (payload) => {
        const user = await api.signup(payload);
        setUser(user);
        navigate("/dashboard");
      }}
      footer={
        <p className="muted">
          已有账号？<Link to="/login">去登录</Link>
        </p>
      }
    />
  );
}
