import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";
import { TaskSummaryCard } from "../components/TaskSummaryCard";
import type { Task } from "../types";

export function TasksPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const returnTo = (location.state as { returnTo?: string; returnLabel?: string } | null)
    ?.returnTo;
  const returnLabel =
    (location.state as { returnTo?: string; returnLabel?: string } | null)?.returnLabel ??
    "返回当前任务";

  useEffect(() => {
    let ignore = false;

    api
      .listTasks()
      .then((response) => {
        if (!ignore) {
          setTasks(response.tasks);
        }
      })
      .catch((nextError) => {
        if (!ignore) {
          setError(nextError instanceof Error ? nextError.message : "无法加载任务列表");
        }
      })
      .finally(() => {
        if (!ignore) {
          setLoading(false);
        }
      });

    return () => {
      ignore = true;
    };
  }, []);

  return (
    <RequireAuth>
      <section className="panel">
        <div className="section-header">
          <div>
            <h1>我的任务</h1>
            <p className="muted">在这里跟踪进行中、待反馈、失败和已完成的任务。</p>
          </div>
          <div className="button-row">
            {returnTo ? (
              <button
                className="secondary-button"
                onClick={() => navigate(returnTo)}
                type="button"
              >
                {returnLabel}
              </button>
            ) : null}
            <Link className="primary-button link-button" to="/dashboard">
              新建报告
            </Link>
          </div>
        </div>
        {loading ? <p>正在加载任务...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {!loading && !tasks.length ? <p className="muted">暂无任务。</p> : null}
        <div className="task-grid">
          {tasks.map((task) => (
            <TaskSummaryCard
              key={task.id}
              state={{ fromTasks: true, returnTo, returnLabel }}
              task={task}
            />
          ))}
        </div>
      </section>
    </RequireAuth>
  );
}
