"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  ActionResult,
  ProjectView,
  archiveProject,
  createProject,
  listProjects,
} from "./actions";

function StatusBadge({ status }: { status: string }) {
  return (
    <span data-testid={`status-${status}`}>
      {status === "archived" ? "已归档" : status === "active" ? "进行中" : status}
    </span>
  );
}

export function ProjectPanel() {
  const [projects, setProjects] = useState<ProjectView[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    const result: ActionResult<ProjectView[]> = await listProjects();
    if (result.ok) {
      setProjects(result.value);
      setLoadError(null);
    } else {
      setLoadError(result.error);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    setBusy(true);
    setActionError(null);
    const result = await createProject(formData);
    setBusy(false);
    if (result.ok) {
      form.reset();
      await reload();
    } else {
      setActionError(result.error);
    }
  }

  async function handleArchive(projectId: string) {
    setBusy(true);
    setActionError(null);
    const result = await archiveProject(projectId);
    setBusy(false);
    if (result.ok) {
      await reload();
    } else {
      setActionError(result.error);
    }
  }

  return (
    <section aria-label="项目">
      <h2>项目</h2>

      <form onSubmit={handleCreate}>
        <label>
          标题{" "}
          <input name="title" required maxLength={200} disabled={busy} />
        </label>
        <label>
          描述{" "}
          <input name="description" maxLength={2000} disabled={busy} />
        </label>
        <button type="submit" disabled={busy}>
          创建项目
        </button>
      </form>

      {actionError !== null && (
        <p role="alert" data-testid="action-error">
          {actionError}
        </p>
      )}

      {loadError !== null ? (
        <p role="alert">无法载入项目:{loadError}</p>
      ) : projects === null ? (
        <p>载入中……</p>
      ) : projects.length === 0 ? (
        <p data-testid="empty-projects">暂无项目 —— 先创建一个</p>
      ) : (
        <ul data-testid="project-list">
          {projects.map((project) => (
            <li key={project.id}>
              <strong>{project.title}</strong>
              {project.description !== null && project.description !== "" && (
                <span> —— {project.description}</span>
              )}
              <StatusBadge status={project.status} />
              {project.status === "active" && (
                <button type="button" disabled={busy} onClick={() => void handleArchive(project.id)}>
                  归档
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
