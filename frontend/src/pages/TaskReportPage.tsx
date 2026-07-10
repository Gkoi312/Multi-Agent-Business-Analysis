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
    pass: "Passed",
    needs_revision: "Needs revision",
    skipped: "Skipped",
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
          setError(nextError instanceof Error ? nextError.message : "Failed to load task");
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
        {loading ? <p>Loading report…</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {task && !loading ? (
          <>
            <div className="section-header">
              <div>
                <h1>Report · {task.company_name}</h1>
                <p className="muted">
                  {task.task_type !== "due_diligence"
                    ? `Task type: ${task.task_type} · `
                    : ""}
                  {task.analysts_preview.length} analyst(s) · v{task.analyst_version}
                </p>
              </div>
              <div className="button-row">
                <Link className="secondary-button link-button" to={`/tasks/${task.id}`}>
                  Task details
                </Link>
                <Link className="secondary-button link-button" to="/tasks">
                  All tasks
                </Link>
                <Link className="primary-button link-button" to="/dashboard">
                  New report
                </Link>
              </div>
            </div>

            {task.status !== "completed" ? (
              <p className="muted">
                Not finished yet ({task.status}). Go back to{" "}
                <Link to={`/tasks/${task.id}`}>task details</Link> to follow up.
              </p>
            ) : null}

            {task.status === "completed" ? (
              <>
                {/* Review status */}
                {task.report_review_status ? (
                  <section className="subsection">
                    <h2>Quality review</h2>
                    <p>
                      Status:{" "}
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
                  <h2>Risk distribution</h2>
                  <p className="muted">
                    Counts of High / Medium / Low entries parsed from the &quot;Risk Assessment&quot; section
                    (overview only).
                  </p>
                  {riskParts.total === 0 ? (
                    <p className="muted">No graded risk lines parsed; qualitative risk text may still appear in the full report.</p>
                  ) : null}
                  <div className="risk-viz">
                    <div className="risk-viz-bar" aria-hidden={riskParts.total === 0}>
                      {riskParts.total > 0 ? (
                        <>
                          <div
                            className="risk-viz-seg risk-viz-high"
                            style={{ width: `${(riskParts.high / riskParts.total) * 100}%` }}
                            title={`High: ${riskParts.high}`}
                          />
                          <div
                            className="risk-viz-seg risk-viz-medium"
                            style={{ width: `${(riskParts.medium / riskParts.total) * 100}%` }}
                            title={`Medium: ${riskParts.medium}`}
                          />
                          <div
                            className="risk-viz-seg risk-viz-low"
                            style={{ width: `${(riskParts.low / riskParts.total) * 100}%` }}
                            title={`Low: ${riskParts.low}`}
                          />
                        </>
                      ) : null}
                    </div>
                    <ul className="risk-viz-legend">
                      <li>
                        <span className="risk-dot risk-viz-high" /> High: {riskParts.high}
                      </li>
                      <li>
                        <span className="risk-dot risk-viz-medium" /> Medium: {riskParts.medium}
                      </li>
                      <li>
                        <span className="risk-dot risk-viz-low" /> Low: {riskParts.low}
                      </li>
                    </ul>
                  </div>
                </section>

                {/* Final recommendations */}
                <section className="subsection">
                  <h2>Final recommendations (snippet)</h2>
                  {task.final_recommendation ? (
                    <p className="report-summary-text">{task.final_recommendation}</p>
                  ) : (
                    <p className="muted">No excerpt yet; see the full report for detail.</p>
                  )}
                </section>

                {/* Downloads */}
                <section className="subsection">
                  <h2>Downloads</h2>
                  {!downloadLinks.length ? (
                    <p className="muted">Report files are not available yet. Retry from task details or contact an admin.</p>
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
                          Download {item.label}
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
