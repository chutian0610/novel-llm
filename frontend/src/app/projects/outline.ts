"use server";

/**
 * Outline actions (ADR-0004): one immutable version per publication. The
 * minimal workbench publishes the work synopsis and constraint lines; volume
 * and chapter plan enrichment comes with the generation workflow.
 */

import { apiClient } from "../../lib/api/client";
import { problemMessage } from "../../lib/api/problems";
import type { components } from "../../lib/api/schema";
import type { ActionResult } from "./actions";

export interface OutlineView {
  id: string;
  versionNumber: number;
  payloadSchemaVersion: number;
  synopsis: string;
  constraints: string[];
  createdAt: string;
}

function toOutlineView(data: components["schemas"]["OutlineResource"]): OutlineView {
  // The API validates plans against the typed contract (ADR-0008), so work
  // and constraints are always present on a published version.
  const plans = data.plans as {
    work?: { synopsis?: string };
    constraints?: { description?: string }[];
  };
  return {
    id: data.id,
    versionNumber: data.version_number,
    payloadSchemaVersion: data.payload_schema_version,
    synopsis: plans.work?.synopsis ?? "",
    constraints: (plans.constraints ?? []).map((constraint) => constraint.description ?? ""),
    createdAt: data.created_at,
  };
}

export async function getCurrentOutline(projectId: string): Promise<ActionResult<OutlineView>> {
  const { data, error } = await apiClient.GET("/api/v1/projects/{project_id}/outline", {
    params: { path: { project_id: projectId } },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toOutlineView(data) };
}

export async function publishOutline(
  projectId: string,
  synopsis: string,
  constraints: string[],
  idempotencyKey: string,
): Promise<ActionResult<OutlineView>> {
  const { data, error } = await apiClient.POST("/api/v1/projects/{project_id}/outline/publish", {
    params: { path: { project_id: projectId }, header: { "Idempotency-Key": idempotencyKey } },
    body: {
      work: { synopsis },
      constraints: constraints.map((description) => ({ description })),
    },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toOutlineView(data) };
}
