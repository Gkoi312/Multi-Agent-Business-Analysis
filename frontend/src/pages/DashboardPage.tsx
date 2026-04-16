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
      setError(nextError instanceof Error ? nextError.message : "无法创建任务");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <RequireAuth>
      <section className="panel">
        <div className="section-header">
          <div>
            <h1>创建尽职调查任务</h1>
            <p className="muted">
              填写公司名称、可选的关注重点和目标岗位背景后，任务会立即开始，
              你可以在任务详情页中持续查看进度。
            </p>
          </div>
          <div className="button-row">
            <Link className="secondary-button link-button" to="/tasks">
              查看全部任务
            </Link>
          </div>
        </div>
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            公司名称
            <input
              onChange={(event) => setCompanyName(event.target.value)}
              required
              value={companyName}
            />
          </label>
          <label>
            关注重点
            <textarea
              onChange={(event) => setFocus(event.target.value)}
              rows={4}
              value={focus}
            />
          </label>
          <label>
            目标岗位
            <input
              onChange={(event) => setTargetRole(event.target.value)}
              value={targetRole}
            />
          </label>
          {error ? <p className="error-text">{error}</p> : null}
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "启动中..." : "生成报告"}
          </button>
        </form>
      </section>
    </RequireAuth>
  );
}
