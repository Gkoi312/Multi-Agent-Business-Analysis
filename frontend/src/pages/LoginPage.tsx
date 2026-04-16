import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { AuthForm } from "../components/AuthForm";
import { useAuth } from "../hooks/useAuth";

export function LoginPage() {
  const navigate = useNavigate();
  const { setUser } = useAuth();

  return (
    <AuthForm
      title="Login"
      submitLabel="Login"
      onSubmit={async (payload) => {
        const user = await api.login(payload);
        setUser(user);
        navigate("/dashboard");
      }}
      footer={
        <p className="muted">
          No account yet? <Link to="/signup">Create one</Link>
        </p>
      }
    />
  );
}
