import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";
import type { Task } from "../types";

function fileNameFromPath(path: string) {
  return path.split(/[/\\]/).pop() ?? path;
}

function getReviewLabel(status: string) {
  const labels: Record<string, string> = {
    pass: "通过",
    needs_revision: "需修改",
    skipped: "已跳过",
  };
  return labels[status] ?? status;
}

export function TaskReportPage() {
  const { taskId = "" } = useParams();
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!taskId) {
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
        const next = await api.getTask(taskId);
        if (!cancelled) {
          setTask(next);
          setError("");
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "加载任务失败");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  const downloadLinks = useMemo(() => {
    if (!task) {
      return [];
    }
    return [task.docx_path, task.pdf_path]
      .filter(Boolean)
      .map((path) => ({
        href: api.buildDownloadUrl(task.id, fileNameFromPath(path)),
        label: fileNameFromPath(path),
      }));
  }, [task]);

  const riskParts = useMemo(() => {
    if (!task) {
      return { high: 0, medium: 0, low: 0, total: 0 };
    }
    const { high, medium, low } = task.risk_summary;
    return { high, medium, low, total: high + medium + low };
  }, [task]);

  return (
    <RequireAuth>
      <section className="panel">
        {loading ? <p>加载报告中…</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {task && !loading ? (
          <>
            <div className="section-header">
              <div>
                <h1>报告 · {task.company_name}</h1>
                <p className="muted">
                  {task.task_type !== "due_diligence"
                    ? `任务类型：${task.task_type} · `
                    : ""}
                  {task.analysts_preview.length} 位分析师 · v{task.analyst_version}
                </p>
              </div>
              <div className="button-row">
                <Link className="secondary-button link-button" to={`/tasks/${task.id}`}>
                  任务详情
                </Link>
                <Link className="secondary-button link-button" to="/tasks">
                  全部任务
                </Link>
                <Link className="primary-button link-button" to="/dashboard">
                  新建报告
                </Link>
              </div>
            </div>

            {task.status !== "completed" ? (
              <p className="muted">
                尚未完成（{task.status}）。返回
                <Link to={`/tasks/${task.id}`}>任务详情</Link>继续跟进。
              </p>
            ) : null}

            {task.status === "completed" ? (
              <>
                {/* Review status */}
                {task.report_review_status ? (
                  <section className="subsection">
                    <h2>质量审核</h2>
                    <p>
                      状态：{" "}
                      <span className={`status-pill status-${task.report_review_status === "pass" ? "completed" : "failed"}`}>
                        {getReviewLabel(task.report_review_status)}
                      </span>
                    </p>
                    {task.report_review_summary ? (
                      <p className="muted">{task.report_review_summary}</p>
                    ) : null}
                  </section>
                ) : null}

                {/* Risk distribution */}
                <section className="subsection">
                  <h2>风险分布</h2>
                  <p className="muted">
                    从「风险评估」章节中解析出的高 / 中 / 低风险条目数量（概览）。
                  </p>
                  {riskParts.total === 0 ? (
                    <p className="muted">未解析到风险等级条目；完整报告中可能仍包含定性风险描述。</p>
                  ) : null}
                  <div className="risk-viz">
                    <div className="risk-viz-bar" aria-hidden={riskParts.total === 0}>
                      {riskParts.total > 0 ? (
                        <>
                          <div
                            className="risk-viz-seg risk-viz-high"
                            style={{ width: `${(riskParts.high / riskParts.total) * 100}%` }}
                            title={`高：${riskParts.high}`}
                          />
                          <div
                            className="risk-viz-seg risk-viz-medium"
                            style={{ width: `${(riskParts.medium / riskParts.total) * 100}%` }}
                            title={`中：${riskParts.medium}`}
                          />
                          <div
                            className="risk-viz-seg risk-viz-low"
                            style={{ width: `${(riskParts.low / riskParts.total) * 100}%` }}
                            title={`低：${riskParts.low}`}
                          />
                        </>
                      ) : null}
                    </div>
                    <ul className="risk-viz-legend">
                      <li>
                        <span className="risk-dot risk-viz-high" /> 高：{riskParts.high}
                      </li>
                      <li>
                        <span className="risk-dot risk-viz-medium" /> 中：{riskParts.medium}
                      </li>
                      <li>
                        <span className="risk-dot risk-viz-low" /> 低：{riskParts.low}
                      </li>
                    </ul>
                  </div>
                </section>

                {/* Final recommendations */}
                <section className="subsection">
                  <h2>最终建议（摘要）</h2>
                  {task.final_recommendation ? (
                    <p className="report-summary-text">{task.final_recommendation}</p>
                  ) : (
                    <p className="muted">暂无摘要，请查看完整报告了解详情。</p>
                  )}
                </section>

                {/* Downloads */}
                <section className="subsection">
                  <h2>下载</h2>
                  {!downloadLinks.length ? (
                    <p className="muted">报告文件暂不可用。请从任务详情页重试或联系管理员。</p>
                  ) : (
                    <div className="button-row">
                      {downloadLinks.map((item) => (
                        <a
                          className="primary-button link-button"
                          href={item.href}
                          key={item.href}
                          rel="noreferrer"
                          target="_blank"
                        >
                          下载 {item.label}
                        </a>
                      ))}
                    </div>
                  )}
                </section>
              </>
            ) : null}
          </>
        ) : null}
      </section>
    </RequireAuth>
  );
}
