"use server";

/**
 * Story Bible actions (ADR-0004): the editable draft (identities + typed
 * entries) and immutable published versions. The generation-facing view
 * reads only the current published version.
 */

import { apiClient } from "../../lib/api/client";
import { problemMessage } from "../../lib/api/problems";
import type { components } from "../../lib/api/schema";
import type { ActionResult } from "./actions";

export interface BibleIdentityView {
  id: string;
  name: string;
  aliases: string[];
  revision: number;
}

export interface BibleEntryView {
  id: string;
  identityId: string;
  entryType: string;
  detail: Record<string, unknown>;
  status: string;
  revision: number;
}

export interface BibleDraftView {
  projectId: string;
  identities: BibleIdentityView[];
  entries: BibleEntryView[];
}

export interface PublishedBibleView {
  versionNumber: number;
  payloadSchemaVersion: number;
  identityCount: number;
  entryCount: number;
}

type DraftResource = components["schemas"]["BibleDraftResource"];
type VersionResource = components["schemas"]["PublishedBibleResource"];

function toDraftView(data: DraftResource): BibleDraftView {
  return {
    projectId: data.project_id,
    identities: data.identities.map((identity) => ({
      id: identity.id,
      name: identity.name,
      aliases: identity.aliases,
      revision: identity.revision,
    })),
    entries: data.entries.map((entry) => ({
      id: entry.id,
      identityId: entry.identity_id,
      entryType: entry.entry_type,
      detail: entry.detail,
      status: entry.status,
      revision: entry.revision,
    })),
  };
}

function toVersionView(data: VersionResource): PublishedBibleView {
  const snapshot = data.entries as unknown as {
    identities?: unknown[];
    entries?: unknown[];
  };
  return {
    versionNumber: data.version_number,
    payloadSchemaVersion: data.payload_schema_version,
    identityCount: Array.isArray(snapshot.identities) ? snapshot.identities.length : 0,
    entryCount: Array.isArray(snapshot.entries) ? snapshot.entries.length : 0,
  };
}

export async function getBibleDraft(projectId: string): Promise<ActionResult<BibleDraftView>> {
  const { data, error } = await apiClient.GET("/api/v1/projects/{project_id}/bible/draft", {
    params: { path: { project_id: projectId } },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toDraftView(data) };
}

export async function createBibleIdentity(
  projectId: string,
  name: string,
  aliases: string[],
  idempotencyKey: string,
): Promise<ActionResult<BibleDraftView>> {
  const { error } = await apiClient.POST("/api/v1/projects/{project_id}/bible/identities", {
    params: { path: { project_id: projectId }, header: { "Idempotency-Key": idempotencyKey } },
    body: { name, aliases },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return getBibleDraft(projectId);
}

// The API's closed entry-type set (ADR-0008): the select in the panel only
// offers these values.
export type BibleEntryType = NonNullable<
  components["schemas"]["CreateEntryRequest"]["entry_type"]
>;

export async function createBibleEntry(
  projectId: string,
  identityId: string,
  entryType: BibleEntryType,
  detail: Record<string, unknown>,
  idempotencyKey: string,
): Promise<ActionResult<BibleDraftView>> {
  const { error } = await apiClient.POST("/api/v1/projects/{project_id}/bible/entries", {
    params: { path: { project_id: projectId }, header: { "Idempotency-Key": idempotencyKey } },
    body: { identity_id: identityId, entry_type: entryType, detail },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return getBibleDraft(projectId);
}

export async function archiveBibleEntry(
  projectId: string,
  entry: BibleEntryView,
): Promise<ActionResult<BibleDraftView>> {
  const { error } = await apiClient.POST("/api/v1/bible/entries/{entry_id}/archive", {
    params: { path: { entry_id: entry.id }, headers: { "If-Match": `"${entry.revision}"` } },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return getBibleDraft(projectId);
}

export async function getCurrentBible(
  projectId: string,
): Promise<ActionResult<PublishedBibleView | null>> {
  const { data, error } = await apiClient.GET("/api/v1/projects/{project_id}/bible", {
    params: { path: { project_id: projectId } },
  });
  if (error !== undefined) {
    // 尚未发布是常态（不是错误）：返回 null 让面板提示发布。
    const body = error as { code?: string };
    if (body.code === "bible_not_found") {
      return { ok: true, value: null };
    }
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toVersionView(data) };
}

export async function updateBibleEntry(
  projectId: string,
  entry: BibleEntryView,
  detail: Record<string, unknown>,
): Promise<ActionResult<BibleDraftView>> {
  const { error } = await apiClient.PATCH("/api/v1/bible/entries/{entry_id}", {
    params: { path: { entry_id: entry.id }, headers: { "If-Match": `"${entry.revision}"` } },
    body: { detail },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return getBibleDraft(projectId);
}

export async function publishBible(
  projectId: string,
  idempotencyKey: string,
): Promise<ActionResult<PublishedBibleView>> {
  const { data, error } = await apiClient.POST("/api/v1/projects/{project_id}/bible/publish", {
    params: { path: { project_id: projectId }, header: { "Idempotency-Key": idempotencyKey } },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toVersionView(data) };
}
