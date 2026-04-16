import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";

export function DashboardPage() {
  const navigate = useNavigate();
  const [companyName, setCompanyName] = useState("");
  const [focus, setFocus] = useState("");
  const [targetRole, setTargetRole] = useState("");
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
        max_analysts: 3,
      });
      navigate(`/tasks/${task.id}`);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Unable to create task");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <RequireAuth>
      <section className="panel">
        <div className="section-header">
          <div>
            <h1>Create due diligence task</h1>
            <p className="muted">
              Submit a company, optional focus areas, and target role context. The task
              starts immediately and can be tracked from the task detail page.
            </p>
          </div>
          <div className="button-row">
            <Link className="secondary-button link-button" to="/tasks">
              View all tasks
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
            Focus
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
          {error ? <p className="error-text">{error}</p> : null}
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "Starting..." : "Generate report"}
          </button>
        </form>
      </section>
    </RequireAuth>
  );
}
