"use client";

/**
 * Outline 面板（最小工作台，简体中文）：显示当前发布的 Outline 版本，并以
 * 一次发布一个不可变新版本（ADR-0004）。最小发布表单覆盖工作梗概与约束；
 * 卷/章/场景/伏笔计划随生成工作流补齐。
 */

import { FormEvent, useCallback, useEffect, useState } from "react";

import { newIdempotencyKey } from "../../lib/idempotency";
import { getCurrentOutline, publishOutline, type OutlineView } from "./outline";

function OutlineDetail({ outline }: { outline: OutlineView }) {
  return (
    <div data-testid="outline-detail">
      <p data-testid="outline-version">
        当前版本:第 {outline.versionNumber} 版（payload schema v
        {outline.payloadSchemaVersion}）
      </p>
      <p>梗概:{outline.synopsis}</p>
      {outline.constraints.length > 0 && (
        <>
          <p>约束:</p>
          <ul>
            {outline.constraints.map((constraint, index) => (
              <li key={index}>{constraint}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function OutlinePanel({ projectId }: { projectId: string }) {
  const [outline, setOutline] = useState<OutlineView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [publishKey, setPublishKey] = useState(() => newIdempotencyKey());

  const reload = useCallback(async () => {
    const result = await getCurrentOutline(projectId);
    if (result.ok) {
      setOutline(result.value);
      setLoadError(null);
    } else {
      setOutline(null);
      setLoadError(result.error);
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function handlePublish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    const synopsis = String(formData.get("synopsis") ?? "").trim();
    const constraints = String(formData.get("constraints") ?? "")
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
    if (!synopsis) {
      return;
    }
    setBusy(true);
    setActionError(null);
    const result = await publishOutline(projectId, synopsis, constraints, publishKey);
    setBusy(false);
    if (result.ok) {
      setOutline(result.value);
      setLoadError(null);
      setPublishKey(newIdempotencyKey());
    } else {
      setActionError(result.error);
    }
  }

  return (
    <section aria-label="Outline">
      <h3>Outline</h3>
      {outline !== null ? <OutlineDetail outline={outline} /> : null}
      {outline === null && loadError !== null && (
        <p data-testid="outline-empty">{loadError}</p>
      )}
      <form onSubmit={handlePublish}>
        <label>
          工作梗概 <textarea name="synopsis" required maxLength={4000} disabled={busy} />
        </label>
        <label>
          约束（一行一条）
          <textarea name="constraints" maxLength={4000} disabled={busy} />
        </label>
        <button type="submit" disabled={busy}>
          发布新版本
        </button>
      </form>
      {actionError !== null && (
        <p role="alert" data-testid="outline-action-error">
          {actionError}
        </p>
      )}
    </section>
  );
}
