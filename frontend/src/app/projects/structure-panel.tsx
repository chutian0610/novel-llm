"use client";

/**
 * 结构面板（最小工作台，简体中文）：渲染一个 Project 的卷与章节树，提供
 * 新建卷/章节、上移/下移重排、跨卷移动——稳定标识 + 可变位置（ADR-0004）。
 * 一次读取整个结构，动作返回刷新后的结构。
 */

import { FormEvent, useCallback, useEffect, useState } from "react";

import { newIdempotencyKey } from "../../lib/idempotency";
import type { ActionResult } from "./actions";
import type { ChapterNode, StructureView, VolumeNode } from "./structure";
import {
  createChapter,
  createVolume,
  getStructure,
  moveChapter,
  reorderChapters,
  reorderVolumes,
} from "./structure";

/** Swap one element toward the front or back of the order. */
function moveInOrder(order: string[], id: string, delta: -1 | 1): string[] | null {
  const index = order.indexOf(id);
  const target = index + delta;
  if (index === -1 || target < 0 || target >= order.length) {
    return null;
  }
  const reordered = [...order];
  [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
  return reordered;
}

function ChapterRow({
  projectId,
  volume,
  chapter,
  otherVolumes,
  busy,
  onBusyAction,
}: {
  projectId: string;
  volume: VolumeNode;
  chapter: ChapterNode;
  otherVolumes: VolumeNode[];
  busy: boolean;
  onBusyAction: (action: () => Promise<ActionResult<StructureView> | null>) => Promise<void>;
}) {
  const chapterIds = volume.chapters.map((entry) => entry.id);
  const canMoveUp = chapter.position > 1;
  const canMoveDown = chapter.position < volume.chapters.length;

  return (
    <li data-testid={`chapter-${chapter.id}`}>
      {chapter.position}. {chapter.title}
      <button
        type="button"
        disabled={!canMoveUp}
        aria-label={`上移 ${chapter.title}`}
        onClick={() =>
          onBusyAction(async () => {
            const order = moveInOrder(chapterIds, chapter.id, -1);
            return order ? await reorderChapters(projectId, volume.id, order) : null;
          })
        }
      >
        上移
      </button>
      <button
        type="button"
        disabled={!canMoveDown}
        aria-label={`下移 ${chapter.title}`}
        onClick={() =>
          onBusyAction(async () => {
            const order = moveInOrder(chapterIds, chapter.id, 1);
            return order ? await reorderChapters(projectId, volume.id, order) : null;
          })
        }
      >
        下移
      </button>
      {otherVolumes.length > 0 && (
        <select
          aria-label={`移动 ${chapter.title} 到`}
          defaultValue=""
          disabled={busy}
          onChange={(event) => {
            const target = event.currentTarget.value;
            event.currentTarget.value = "";
            if (target) {
              void onBusyAction(async () => await moveChapter(projectId, chapter.id, target));
            }
          }}
        >
          <option value="">移动到…</option>
          {otherVolumes.map((other) => (
            <option key={other.id} value={other.id}>
              {other.title}
            </option>
          ))}
        </select>
      )}
    </li>
  );
}

export function StructurePanel({ projectId }: { projectId: string }) {
  const [structure, setStructure] = useState<StructureView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [volumeKey, setVolumeKey] = useState(() => newIdempotencyKey());

  const reload = useCallback(async () => {
    const result = await getStructure(projectId);
    if (result.ok) {
      setStructure(result.value);
      setLoadError(null);
    } else {
      setLoadError(result.error);
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const runAction = useCallback(
    async (action: () => Promise<ActionResult<StructureView> | null>) => {
      setBusy(true);
      setActionError(null);
      const result = await action();
      setBusy(false);
      if (result === null) {
        return;
      }
      if (result.ok) {
        setStructure(result.value);
      } else {
        setActionError(result.error);
      }
    },
    [],
  );

  async function handleCreateVolume(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const title = String(new FormData(form).get("title") ?? "").trim();
    if (!title) {
      return;
    }
    await runAction(async () => {
      const result = await createVolume(projectId, title, volumeKey);
      if (result.ok) {
        form.reset();
        setVolumeKey(newIdempotencyKey());
      }
      return result;
    });
  }

  async function handleCreateChapter(volumeId: string, event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const title = String(new FormData(form).get("title") ?? "").trim();
    if (!title) {
      return;
    }
    await runAction(async () => {
      const result = await createChapter(projectId, volumeId, title, newIdempotencyKey());
      if (result.ok) {
        form.reset();
      }
      return result;
    });
  }

  if (loadError !== null) {
    return (
      <section aria-label="结构">
        <h3>结构</h3>
        <p role="alert">无法载入结构:{loadError}</p>
      </section>
    );
  }
  if (structure === null) {
    return (
      <section aria-label="结构">
        <h3>结构</h3>
        <p>载入中……</p>
      </section>
    );
  }

  const volumeIds = structure.volumes.map((volume) => volume.id);

  return (
    <section aria-label="结构">
      <h3>结构</h3>
      <form onSubmit={handleCreateVolume}>
        <label>
          卷名 <input name="title" required maxLength={200} disabled={busy} />
        </label>
        <button type="submit" disabled={busy}>
          新建卷
        </button>
      </form>
      {actionError !== null && (
        <p role="alert" data-testid="structure-action-error">
          {actionError}
        </p>
      )}
      {structure.volumes.length === 0 ? (
        <p>还没有卷。</p>
      ) : (
        <ol data-testid="volume-list">
          {structure.volumes.map((volume) => (
            <li key={volume.id} data-testid={`volume-${volume.id}`}>
              {volume.position}. {volume.title}
              <button
                type="button"
                disabled={busy || volume.position === 1}
                aria-label={`上移 ${volume.title}`}
                onClick={() =>
                  runAction(async () => {
                    const order = moveInOrder(volumeIds, volume.id, -1);
                    return order ? await reorderVolumes(projectId, order) : null;
                  })
                }
              >
                上移
              </button>
              <button
                type="button"
                disabled={busy || volume.position === structure.volumes.length}
                aria-label={`下移 ${volume.title}`}
                onClick={() =>
                  runAction(async () => {
                    const order = moveInOrder(volumeIds, volume.id, 1);
                    return order ? await reorderVolumes(projectId, order) : null;
                  })
                }
              >
                下移
              </button>
              <form onSubmit={(event) => handleCreateChapter(volume.id, event)}>
                <label>
                  章节名 <input name="title" required maxLength={200} disabled={busy} />
                </label>
                <button type="submit" disabled={busy}>
                  新建章节
                </button>
              </form>
              {volume.chapters.length === 0 ? (
                <p>本卷还没有章节。</p>
              ) : (
                <ul>
                  {volume.chapters.map((chapter) => (
                    <ChapterRow
                      key={chapter.id}
                      projectId={projectId}
                      volume={volume}
                      chapter={chapter}
                      otherVolumes={structure.volumes.filter((other) => other.id !== volume.id)}
                      busy={busy}
                      onBusyAction={runAction}
                    />
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
