import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";
import type { Task, TaskMetrics } from "../types";

function fileNameFromPath(path: string) {
  return path.split(/[/\\]/).pop() ?? path;
}

function getReviewLabel(status: string) {
  const labels: Record<string, string> = {
    pass: "通过",
    needs_revision: "需要修改",
    skipped: "已跳过",
  };
  return labels[status] ?? status;
}

function renderMarkdown(md: string): string {
  let html = md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/^### (.+)$/gm, "<h4>$1</h4>");
  html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/\n\n+/g, "</p><p>");
  html = html.replace(/\n/g, "<br>");

  if (!html.startsWith("<")) {
    html = `<p>${html}</p>`;
  }
  return html;
}

export function TaskReportPage() {
  const { taskId = "" } = useParams();
  const [task, setTask] = useState<Task | null>(null);
  const [metrics, setMetrics] = useState<TaskMetrics | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!taskId) return undefined;
    let cancelled = false;

    (async () => {
      try {
        const [nextTask, nextMetrics] = await Promise.all([
          api.getTask(taskId),
          api.getTaskMetrics(taskId),
        ]);
        if (!cancelled) {
          setTask(nextTask);
          setMetrics(nextMetrics);
          setError("");
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "加载任务失败");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [taskId]);

  const downloadLinks = useMemo(() => {
    if (!task) return [];
    return [task.docx_path, task.pdf_path]
      .filter(Boolean)
      .map((path) => ({
        href: api.buildDownloadUrl(task.id, fileNameFromPath(path)),
        label: fileNameFromPath(path),
      }));
  }, [task]);

  const riskParts = useMemo(() => {
    if (!task) return { high: 0, medium: 0, low: 0, total: 0 };
    const { high, medium, low } = task.risk_summary;
    return { high, medium, low, total: high + medium + low };
  }, [task]);

  return (
    <RequireAuth>
      <section className="panel">
        {loading ? <p>加载报告中...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {task && !loading ? (
          <>
            <div className="section-header">
              <div>
                <h1>报告 · {task.company_name}</h1>
                <p className="muted">
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
                当前任务尚未完成（{task.status}）。请返回 <Link to={`/tasks/${task.id}`}>任务详情</Link> 继续跟进。
              </p>
            ) : null}

            {task.status === "completed" ? (
              <>
                {task.report_review_status && task.report_review_status !== "pass" ? (
                  <section className="subsection">
                    <h2>质量审核</h2>
                    <p>
                      状态： <span className="status-pill status-failed">{getReviewLabel(task.report_review_status)}</span>
                    </p>
                    {task.report_review_summary ? <p className="muted">{task.report_review_summary}</p> : null}
                  </section>
                ) : null}

                <section className="subsection">
                  <h2>风险分布</h2>
                  <p className="muted">从风险评估章节解析出的高 / 中 / 低风险条目数量。</p>
                  {riskParts.total === 0 ? <p className="muted">未解析到结构化风险等级条目。</p> : null}
                  <div className="risk-viz">
                    <div className="risk-viz-bar" aria-hidden={riskParts.total === 0}>
                      {riskParts.total > 0 ? (
                        <>
                          <div className="risk-viz-seg risk-viz-high" style={{ width: `${(riskParts.high / riskParts.total) * 100}%` }} />
                          <div className="risk-viz-seg risk-viz-medium" style={{ width: `${(riskParts.medium / riskParts.total) * 100}%` }} />
                          <div className="risk-viz-seg risk-viz-low" style={{ width: `${(riskParts.low / riskParts.total) * 100}%` }} />
                        </>
                      ) : null}
                    </div>
                    <ul className="risk-viz-legend">
                      <li><span className="risk-dot risk-viz-high" /> 高：{riskParts.high}</li>
                      <li><span className="risk-dot risk-viz-medium" /> 中：{riskParts.medium}</li>
                      <li><span className="risk-dot risk-viz-low" /> 低：{riskParts.low}</li>
                    </ul>
                  </div>
                </section>

                <section className="subsection">
                  <h2>最终建议摘要</h2>
                  {task.final_recommendation ? (
                    <div
                      className="report-summary-text markdown-body"
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(task.final_recommendation) }}
                    />
                  ) : (
                    <p className="muted">暂无摘要，请下载完整报告查看详情。</p>
                  )}
                </section>

                {metrics ? (
                  <section className="subsection">
                    <h2>执行指标</h2>
                    <div className="task-meta-grid">
                      <div><strong>总耗时：</strong> {(metrics.total_latency_ms / 1000).toFixed(1)}s</div>
                      <div><strong>LLM 调用：</strong> {metrics.call_count}</div>
                      <div><strong>输入 Token：</strong> {metrics.total_prompt_tokens.toLocaleString()}</div>
                      <div><strong>输出 Token：</strong> {metrics.total_completion_tokens.toLocaleString()}</div>
                      <div><strong>总 Token：</strong> {metrics.total_tokens.toLocaleString()}</div>
                      <div><strong>预计成本：</strong> ${metrics.estimated_cost_usd.toFixed(4)}</div>
                    </div>
                  </section>
                ) : null}

                <section className="subsection">
                  <h2>下载</h2>
                  {!downloadLinks.length ? (
                    <p className="muted">报告文件暂不可用，请从任务详情页重试或联系管理员。</p>
                  ) : (
                    <div className="button-row">
                      {downloadLinks.map((item) => (
                        <a className="primary-button link-button" href={item.href} key={item.href} rel="noreferrer" target="_blank">
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
