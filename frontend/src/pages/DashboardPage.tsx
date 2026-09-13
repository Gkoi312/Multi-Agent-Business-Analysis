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
      setError(nextError instanceof Error ? nextError.message : "创建任务失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <RequireAuth>
      <section className="panel">
        <div className="section-header">
          <div>
            <h1>创建研究任务</h1>
            <p className="muted">面向 AI 科技公司的商业尽调工作流，提交后会先生成分析师团队。</p>
          </div>
          <div className="button-row">
            <Link className="secondary-button link-button" to="/tasks">
              全部任务
            </Link>
          </div>
        </div>
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            公司名称
            <input
              onChange={(event) => setCompanyName(event.target.value)}
              placeholder="例如：OpenAI"
              required
              value={companyName}
            />
          </label>
          <label>
            关注领域
            <textarea
              onChange={(event) => setFocus(event.target.value)}
              placeholder="例如：商业模式、增长质量、竞争壁垒、合规风险"
              rows={4}
              value={focus}
            />
          </label>
          <label>
            目标角色
            <input
              onChange={(event) => setTargetRole(event.target.value)}
              placeholder="例如：投资经理、战略负责人、产品负责人"
              value={targetRole}
            />
          </label>
          <label>
            分析师数量：{maxAnalysts}
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
            {submitting ? "启动中..." : "生成报告"}
          </button>
        </form>
      </section>
    </RequireAuth>
  );
}
