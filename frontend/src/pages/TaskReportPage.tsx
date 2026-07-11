import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";
import type { Task, TaskMetrics } from "../types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

/** Minimal markdown → HTML for report snippets (headers, bold, italic, lists). */
function renderMarkdown(md: string): string {
  let html = md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Bold / italic
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

  // Headers (### first, then ##)
  html = html.replace(/^### (.+)$/gm, "<h4>$1</h4>");
  html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");

  // Numbered list items — wrap block in <ol>
  html = html.replace(
    /((?:^\d+[.)]\s+.+(?:\n|$))+)/gm,
    (block) => {
      const items = block
        .trim()
        .split("\n")
        .map((line) => line.replace(/^\d+[.)]\s+(.+)$/, "<li>$1</li>"))
        .join("");
      return `<ol>${items}</ol>`;
    }
  );

  // Unordered list items — wrap block in <ul>
  html = html.replace(
    /((?:^[-*] .+(?:\n|$))+)/gm,
    (block) => {
      const items = block
        .trim()
        .split("\n")
        .map((line) => line.replace(/^[-*] (.+)$/, "<li>$1</li>"))
        .join("");
      return `<ul>${items}</ul>`;
    }
  );

  // Double-newline → paragraph break
  html = html.replace(/\n\n+/g, "</p><p>");
  // Remaining single newlines → <br>
  html = html.replace(/\n/g, "<br>");

  // Strip <br> between block-level elements (heading→list, list→heading, etc.)
  html = html.replace(
    /<\/(h[3-6]|ol|ul|li|blockquote|div)><br><(h[3-6]|ol|ul|blockquote|div)/g,
    "</$1><$2"
  );

  // Wrap in a single <p> if not already wrapped
  if (!html.startsWith("<")) {
    html = `<p>${html}</p>`;
  }

  return html;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

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
          if (nextMetrics) setMetrics(nextMetrics);
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
    return () => { cancelled = true; };
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
        {loading ? <p>加载报告中…</p> : null}
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
                尚未完成（{task.status}）。返回{" "}
                <Link to={`/tasks/${task.id}`}>任务详情</Link>继续跟进。
              </p>
            ) : null}

            {task.status === "completed" ? (
              <>
                {/* Quality review — only show when not passed (pass is uninteresting) */}
                {task.report_review_status && task.report_review_status !== "pass" ? (
                  <section className="subsection">
                    <h2>质量审核</h2>
                    <p>
                      状态：{" "}
                      <span className={`status-pill status-failed`}>
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
                      <li><span className="risk-dot risk-viz-high" /> 高：{riskParts.high}</li>
                      <li><span className="risk-dot risk-viz-medium" /> 中：{riskParts.medium}</li>
                      <li><span className="risk-dot risk-viz-low" /> 低：{riskParts.low}</li>
                    </ul>
                  </div>
                </section>

                {/* Final recommendations */}
                <section className="subsection">
                  <h2>最终建议（摘要）</h2>
                  {task.final_recommendation ? (
                    <div
                      className="report-summary-text markdown-body"
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(task.final_recommendation) }}
                    />
                  ) : (
                    <p className="muted">暂无摘要，请查看完整报告了解详情。</p>
                  )}
                </section>

                {/* Execution metrics */}
                {metrics ? (
                  <section className="subsection">
                    <h2>执行指标</h2>
                    <div className="task-meta-grid">
                      <div><strong>总耗时：</strong> {(metrics.total_latency_ms / 1000).toFixed(1)}s</div>
                      <div><strong>LLM 调用：</strong> {metrics.call_count}</div>
                      <div><strong>输入 Token：</strong> {metrics.total_prompt_tokens.toLocaleString()}</div>
                      <div><strong>输出 Token：</strong> {metrics.total_completion_tokens.toLocaleString()}</div>
                      <div><strong>总 Token：</strong> {metrics.total_tokens.toLocaleString()}</div>
                      <div>
                        <strong>预估成本：</strong> ${metrics.estimated_cost_usd.toFixed(4)}
                        {metrics.over_budget ? <span className="error-text"> ⚠️ 超出预算</span> : null}
                      </div>
                    </div>
                  </section>
                ) : null}

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
