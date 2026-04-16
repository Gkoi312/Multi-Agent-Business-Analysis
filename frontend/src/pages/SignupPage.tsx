import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { AuthForm } from "../components/AuthForm";
import { useAuth } from "../hooks/useAuth";

export function SignupPage() {
  const navigate = useNavigate();
  const { setUser } = useAuth();

  return (
    <AuthForm
      title="Create account"
      submitLabel="Sign up"
      onSubmit={async (payload) => {
        const user = await api.signup(payload);
        setUser(user);
        navigate("/dashboard");
      }}
      footer={
        <p className="muted">
          Already have an account? <Link to="/login">Login here</Link>
        </p>
      }
    />
  );
}
