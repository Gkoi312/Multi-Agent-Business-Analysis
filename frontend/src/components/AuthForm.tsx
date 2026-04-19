import { useState } from "react";

type AuthFormProps = {
  title: string;
  submitLabel: string;
  onSubmit: (payload: { username: string; password: string }) => Promise<void>;
  footer: React.ReactNode;
};

export function AuthForm({ title, submitLabel, onSubmit, footer }: AuthFormProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await onSubmit({ username, password });
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Request failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="auth-card">
      <h1>{title}</h1>
      <form className="form-stack" onSubmit={handleSubmit}>
        <label>
          Username
          <input
            autoComplete="username"
            onChange={(event) => setUsername(event.target.value)}
            required
            value={username}
          />
        </label>
        <label>
          Password
          <input
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
            required
            type="password"
            value={password}
          />
        </label>
        {error ? <p className="error-text">{error}</p> : null}
        <button className="primary-button" disabled={submitting} type="submit">
          {submitting ? "Submitting…" : submitLabel}
        </button>
      </form>
      <div className="auth-footer">{footer}</div>
    </section>
  );
}
