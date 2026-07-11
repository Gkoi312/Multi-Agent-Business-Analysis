import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";

export function DashboardPage() {
  const navigate = useNavigate();
  const [companyName, setCompanyName] = useState("");
  const [focus, setFocus] = useState("");
  const [targetRole, setTargetRole] = useState("");
  const [maxAnalysts, setMaxAnalysts] = useState(3);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const { task } = await api.createReport({
        company_name: companyName,
        focus,
        target_role: targetRole,
        max_analysts: maxAnalysts,
      });
      navigate(`/tasks/${task.id}`);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to create task");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <RequireAuth>
      <section className="panel">
        <div className="section-header">
          <div>
            <h1>Create research task</h1>
            <p className="muted">AI 科技公司调研 — 提交后立即开始运行。</p>
          </div>
          <div className="button-row">
            <Link className="secondary-button link-button" to="/tasks">
              All tasks
            </Link>
          </div>
        </div>
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            Company name
            <input
              onChange={(event) => setCompanyName(event.target.value)}
              required
              value={companyName}
            />
          </label>
          <label>
            Focus areas
            <textarea
              onChange={(event) => setFocus(event.target.value)}
              rows={4}
              value={focus}
            />
          </label>
          <label>
            Target role
            <input
              onChange={(event) => setTargetRole(event.target.value)}
              value={targetRole}
            />
          </label>
          <label>
            Max analysts: {maxAnalysts}
            <input
              max={8}
              min={1}
              onChange={(event) => setMaxAnalysts(Number(event.target.value))}
              type="range"
              value={maxAnalysts}
            />
          </label>
          {error ? <p className="error-text">{error}</p> : null}
          <button
            className="primary-button"
            disabled={submitting}
            type="submit"
          >
            {submitting ? "Starting…" : "Generate report"}
          </button>
        </form>
      </section>
    </RequireAuth>
  );
}
