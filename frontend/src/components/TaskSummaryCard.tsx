import { Link } from "react-router-dom";

import type { Task } from "../types";

type TaskSummaryCardProps = {
  task: Task;
  state?: Record<string, unknown>;
};

function getStatusLabel(status: string) {
  const statusLabels: Record<string, string> = {
    pending: "待开始",
    blocked: "已阻塞",
    running_generation: "生成中",
    awaiting_feedback: "待反馈",
    running_feedback: "处理反馈中",
    failed: "失败",
    completed: "已完成",
  };
  return statusLabels[status] ?? status;
}

export function TaskSummaryCard({ task, state }: TaskSummaryCardProps) {
  return (
    <article className="panel task-card">
      <div className="task-card-header">
        <div>
          <h3>{task.company_name}</h3>
          <p className="muted">任务 ID：{task.id}</p>
        </div>
        <span className={`status-pill status-${task.status}`}>{getStatusLabel(task.status)}</span>
      </div>
      <p>
        <strong>关注重点：</strong> {task.focus || "默认尽职调查重点"}
      </p>
      <p>
        <strong>目标岗位：</strong> {task.target_role || "未指定"}
      </p>
      <p>
        <strong>更新时间：</strong> {new Date(task.updated_at * 1000).toLocaleString()}
      </p>
      <Link className="secondary-button link-button" state={state} to={`/tasks/${task.id}`}>
        查看详情
      </Link>
    </article>
  );
}
