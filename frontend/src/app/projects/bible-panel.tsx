"use client";

/**
 * Bible 面板（最小工作台，简体中文）：编辑草稿（身份 + 九类条目）并发布为
 * 不可变版本（ADR-0004）。发布面向生成；草稿永远不是生成引用的对象。
 * 最小条目表单收集各类型的主字段（描述/问题）；关系条目另选从/到身份。
 */

import { FormEvent, useCallback, useEffect, useState } from "react";

import { newIdempotencyKey } from "../../lib/idempotency";
import {
  archiveBibleEntry,
  createBibleEntry,
  createBibleIdentity,
  getBibleDraft,
  getCurrentBible,
  publishBible,
  updateBibleEntry,
  type BibleDraftView,
  type BibleEntryType,
  type BibleEntryView,
  type PublishedBibleView,
} from "./bible";
import type { ActionResult } from "./actions";

const ENTRY_TYPE_LABELS: Record<string, string> = {
  character: "角色",
  relationship: "关系",
  location: "地点",
  organization: "组织",
  item: "物品",
  rule: "规则",
  event: "事件",
  style: "文风",
  open_question: "未决问题",
};

const ENTRY_TYPES = Object.keys(ENTRY_TYPE_LABELS) as BibleEntryType[];

/** Each entry type's primary text field — the minimal form collects this. */
const PRIMARY_DETAIL_FIELD: Record<string, string> = {
  character: "description",
  relationship: "description",
  location: "description",
  organization: "description",
  item: "description",
  rule: "description",
  event: "description",
  style: "description",
  open_question: "question",
};

function EntryRow({
  projectId,
  entry,
  busy,
  onAction,
}: {
  projectId: string;
  entry: BibleEntryView;
  busy: boolean;
  onAction: (action: () => Promise<ActionResult<BibleDraftView>>) => Promise<void>;
}) {
  const primaryField = PRIMARY_DETAIL_FIELD[entry.entryType] ?? "description";
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(() => String(entry.detail[primaryField] ?? ""));

  if (!editing) {
    return (
      <>
        {String(entry.detail[primaryField] ?? "")}
        <button
          type="button"
          disabled={busy}
          aria-label={`编辑 ${ENTRY_TYPE_LABELS[entry.entryType] ?? entry.entryType} 条目`}
          onClick={() => setEditing(true)}
        >
          编辑
        </button>
        <button
          type="button"
          disabled={busy}
          aria-label={`归档 ${ENTRY_TYPE_LABELS[entry.entryType] ?? entry.entryType} 条目`}
          onClick={() =>
            onAction(async () => await archiveBibleEntry(projectId, entry))
          }
        >
          归档
        </button>
      </>
    );
  }
  return (
    <>
      <input
        value={text}
        maxLength={4000}
        disabled={busy}
        aria-label={`编辑 ${ENTRY_TYPE_LABELS[entry.entryType] ?? entry.entryType} 条目内容`}
        onChange={(event) => setText(event.currentTarget.value)}
      />
      <button
        type="button"
        disabled={busy || !text.trim()}
        onClick={() =>
          onAction(async () => {
            const result = await updateBibleEntry(projectId, entry, {
              ...entry.detail,
              [primaryField]: text.trim(),
            });
            if (result.ok) {
              setEditing(false);
            }
            return result;
          })
        }
      >
        保存
      </button>
      <button type="button" disabled={busy} onClick={() => setEditing(false)}>
        取消
      </button>
    </>
  );
}

export function BiblePanel({ projectId }: { projectId: string }) {
  const [draft, setDraft] = useState<BibleDraftView | null>(null);
  const [published, setPublished] = useState<PublishedBibleView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [identityKey, setIdentityKey] = useState(() => newIdempotencyKey());
  const [publishKey, setPublishKey] = useState(() => newIdempotencyKey());

  const reload = useCallback(async () => {
    const draftResult = await getBibleDraft(projectId);
    const versionResult = await getCurrentBible(projectId);
    if (draftResult.ok) {
      setDraft(draftResult.value);
      setLoadError(null);
    } else {
      setLoadError(draftResult.error);
    }
    if (versionResult.ok) {
      setPublished(versionResult.value);
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const runAction = useCallback(
    async (action: () => Promise<ActionResult<BibleDraftView>>) => {
      setBusy(true);
      setActionError(null);
      const result = await action();
      setBusy(false);
      if (result.ok) {
        setDraft(result.value);
      } else {
        setActionError(result.error);
      }
    },
    [],
  );

  async function handleCreateIdentity(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    const name = String(formData.get("name") ?? "").trim();
    const aliases = String(formData.get("aliases") ?? "")
      .split(/[,，]/)
      .map((alias) => alias.trim())
      .filter((alias) => alias.length > 0);
    if (!name) {
      return;
    }
    await runAction(async () => {
      const result = await createBibleIdentity(projectId, name, aliases, identityKey);
      if (result.ok) {
        form.reset();
        setIdentityKey(newIdempotencyKey());
      }
      return result;
    });
  }

  async function handleCreateEntry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    const identityId = String(formData.get("identity_id") ?? "");
    const entryType = String(formData.get("entry_type") ?? "");
    const text = String(formData.get("primary") ?? "").trim();
    if (!identityId || !entryType || !text) {
      return;
    }
    const detail: Record<string, unknown> = { [PRIMARY_DETAIL_FIELD[entryType]]: text };
    if (entryType === "relationship") {
      const fromIdentity = String(formData.get("from_identity") ?? "");
      const toIdentity = String(formData.get("to_identity") ?? "");
      if (!fromIdentity || !toIdentity) {
        return;
      }
      detail.from_identity = fromIdentity;
      detail.to_identity = toIdentity;
    }
    await runAction(async () => {
      const result = await createBibleEntry(
        projectId,
        identityId,
        entryType as BibleEntryType,
        detail,
        newIdempotencyKey(),
      );
      if (result.ok) {
        form.reset();
      }
      return result;
    });
  }

  async function handlePublish() {
    setBusy(true);
    setActionError(null);
    const result = await publishBible(projectId, publishKey);
    setBusy(false);
    if (result.ok) {
      setPublished(result.value);
      setPublishKey(newIdempotencyKey());
      await reload();
    } else {
      setActionError(result.error);
    }
  }

  if (loadError !== null) {
    return (
      <section aria-label="Story Bible">
        <h3>Story Bible</h3>
        <p role="alert">无法载入草稿:{loadError}</p>
      </section>
    );
  }

  return (
    <section aria-label="Story Bible">
      <h3>Story Bible</h3>
      {published !== null ? (
        <p data-testid="bible-version">
          当前发布版本:第 {published.versionNumber} 版（{published.identityCount} 个身份、
          {published.entryCount} 条冻结条目）
        </p>
      ) : (
        <p data-testid="bible-empty">尚未发布 Bible:生成引用只指向发布版本。</p>
      )}

      <form onSubmit={handleCreateIdentity}>
        <label>
          身份名 <input name="name" required maxLength={200} disabled={busy} />
        </label>
        <label>
          别名（逗号分隔） <input name="aliases" disabled={busy} />
        </label>
        <button type="submit" disabled={busy}>
          新建身份
        </button>
      </form>

      {draft !== null && (
        <form onSubmit={handleCreateEntry}>
          <label>
            所属身份
            <select name="identity_id" required disabled={busy} defaultValue="">
              <option value="">选择…</option>
              {draft.identities.map((identity) => (
                <option key={identity.id} value={identity.id}>
                  {identity.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            条目类型
            <select name="entry_type" required disabled={busy} defaultValue="">
              <option value="">选择…</option>
              {ENTRY_TYPES.map((entryType) => (
                <option key={entryType} value={entryType}>
                  {ENTRY_TYPE_LABELS[entryType]}
                </option>
              ))}
            </select>
          </label>
          <label>
            内容 <textarea name="primary" required maxLength={4000} disabled={busy} />
          </label>
          {draft.identities.length >= 2 && (
            <>
              <label>
                关系从
                <select name="from_identity" defaultValue="" disabled={busy}>
                  <option value="">选择…</option>
                  {draft.identities.map((identity) => (
                    <option key={identity.id} value={identity.id}>
                      {identity.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                关系到
                <select name="to_identity" defaultValue="" disabled={busy}>
                  <option value="">选择…</option>
                  {draft.identities.map((identity) => (
                    <option key={identity.id} value={identity.id}>
                      {identity.name}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          <button type="submit" disabled={busy}>
            新建条目
          </button>
        </form>
      )}

      {actionError !== null && (
        <p role="alert" data-testid="bible-action-error">
          {actionError}
        </p>
      )}

      {draft !== null && draft.entries.length > 0 && (
        <ul data-testid="bible-entries">
          {draft.entries.map((entry) => (
            <li key={entry.id} data-testid={`bible-entry-${entry.id}`}>
              【{ENTRY_TYPE_LABELS[entry.entryType] ?? entry.entryType}】
              <EntryRow projectId={projectId} entry={entry} busy={busy} onAction={runAction} />
            </li>
          ))}
        </ul>
      )}

      <button type="button" disabled={busy} onClick={handlePublish} data-testid="publish-bible">
        发布新版本
      </button>
    </section>
  );
}
