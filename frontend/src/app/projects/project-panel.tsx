"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { newIdempotencyKey } from "../../lib/idempotency";
import {
  ActionResult,
  ProjectView,
  archiveProject,
  createProject,
  getProject,
  listProjects,
} from "./actions";
import { BiblePanel } from "./bible-panel";
import { OutlinePanel } from "./outline-panel";
import { ProjectEventsPanel } from "./project-events-panel";
import { StructurePanel } from "./structure-panel";

function StatusBadge({ status }: { status: string }) {
  return (
    <span data-testid={`status-${status}`}>
      {status === "archived" ? "已归档" : status === "active" ? "进行中" : status}
    </span>
  );
}

function ProjectDetail({ project }: { project: ProjectView }) {
  return (
    <div data-testid="project-detail">
      <dl>
        <dt>描述</dt>
        <dd>{project.description ?? "（无）"}</dd>
        <dt>语言</dt>
        <dd>{project.language}</dd>
        <dt>创建于</dt>
        <dd>{project.createdAt}</dd>
        <dt>更新于</dt>
        <dd>{project.updatedAt}</dd>
      </dl>
      {project.status === "active" && (
        <>
          <StructurePanel projectId={project.id} />
          <OutlinePanel projectId={project.id} />
          <BiblePanel projectId={project.id} />
        </>
      )}
      <ProjectEventsPanel projectId={project.id} />
    </div>
  );
}

export function ProjectPanel() {
  const [projects, setProjects] = useState<ProjectView[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<ProjectView | null>(null);
  // One key per create intent: a failed submission retries with the same key
  // (replays to the original Project); success starts a new intent.
  const [createKey, setCreateKey] = useState(() => newIdempotencyKey());

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
    const result = await createProject(formData, createKey);
    setBusy(false);
    if (result.ok) {
      form.reset();
      setCreateKey(newIdempotencyKey());
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
      if (detail?.id === projectId) {
        setDetail(result.ok ? result.value : null);
      }
      await reload();
    } else {
      setActionError(result.error);
    }
  }

  async function toggleDetail(projectId: string) {
    if (detail?.id === projectId) {
      setDetail(null);
      return;
    }
    setBusy(true);
    setActionError(null);
    const result = await getProject(projectId);
    setBusy(false);
    if (result.ok) {
      setDetail(result.value);
    } else {
      setActionError(result.error);
    }
  }

  return (
    <section aria-label="项目">
      <h2>项目</h2>

      <form onSubmit={handleCreate}>
        <label>
          标题 <input name="title" required maxLength={200} disabled={busy} />
        </label>
        <label>
          描述 <input name="description" maxLength={2000} disabled={busy} />
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
              <button
                type="button"
                disabled={busy}
                onClick={() => void toggleDetail(project.id)}
              >
                {detail?.id === project.id ? "收起" : "详情"}
              </button>
              {project.status === "active" && (
                <button type="button" disabled={busy} onClick={() => void handleArchive(project.id)}>
                  归档
                </button>
              )}
              {detail?.id === project.id && <ProjectDetail project={detail} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
