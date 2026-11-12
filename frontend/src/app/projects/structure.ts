"use server";

/**
 * Structure actions: Volumes and Chapters with stable identities and mutable
 * positions (ADR-0004). The workbench reads the whole structure in one call
 * and mutates it through domain actions (create, reorder, move).
 */

import { apiClient } from "../../lib/api/client";
import { problemMessage } from "../../lib/api/problems";
import type { components } from "../../lib/api/schema";
import type { ActionResult } from "./actions";

export interface ChapterNode {
  id: string;
  title: string;
  volumeId: string;
  position: number;
}

export interface VolumeNode {
  id: string;
  title: string;
  position: number;
  chapters: ChapterNode[];
}

export interface StructureView {
  projectId: string;
  volumes: VolumeNode[];
}

function toStructureView(data: components["schemas"]["StructureResource"]): StructureView {
  return {
    projectId: data.project_id,
    volumes: data.volumes.map((volume) => ({
      id: volume.id,
      title: volume.title,
      position: volume.position,
      chapters: volume.chapters.map((chapter) => ({
        id: chapter.id,
        title: chapter.title,
        volumeId: chapter.volume_id,
        position: chapter.position,
      })),
    })),
  };
}

export async function getStructure(projectId: string): Promise<ActionResult<StructureView>> {
  const { data, error } = await apiClient.GET("/api/v1/projects/{project_id}/structure", {
    params: { path: { project_id: projectId } },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toStructureView(data) };
}

export async function createVolume(
  projectId: string,
  title: string,
  idempotencyKey: string,
): Promise<ActionResult<StructureView>> {
  const { error } = await apiClient.POST("/api/v1/projects/{project_id}/volumes", {
    params: { path: { project_id: projectId }, header: { "Idempotency-Key": idempotencyKey } },
    body: { title },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return getStructure(projectId);
}

export async function createChapter(
  projectId: string,
  volumeId: string,
  title: string,
  idempotencyKey: string,
): Promise<ActionResult<StructureView>> {
  const { error } = await apiClient.POST("/api/v1/projects/{project_id}/chapters", {
    params: { path: { project_id: projectId }, header: { "Idempotency-Key": idempotencyKey } },
    body: { volume_id: volumeId, title },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return getStructure(projectId);
}

export async function reorderVolumes(
  projectId: string,
  orderedVolumeIds: string[],
): Promise<ActionResult<StructureView>> {
  const { data, error } = await apiClient.POST("/api/v1/projects/{project_id}/volumes/reorder", {
    params: { path: { project_id: projectId } },
    body: { ordered_volume_ids: orderedVolumeIds },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toStructureView(data) };
}

export async function reorderChapters(
  projectId: string,
  volumeId: string,
  orderedChapterIds: string[],
): Promise<ActionResult<StructureView>> {
  const { data, error } = await apiClient.POST("/api/v1/volumes/{volume_id}/chapters/reorder", {
    params: { path: { volume_id: volumeId } },
    body: { ordered_chapter_ids: orderedChapterIds },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toStructureView(data) };
}

export async function moveChapter(
  projectId: string,
  chapterId: string,
  toVolumeId: string,
): Promise<ActionResult<StructureView>> {
  const { error } = await apiClient.POST("/api/v1/chapters/{chapter_id}/move", {
    params: { path: { chapter_id: chapterId } },
    body: { to_volume_id: toVolumeId },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return getStructure(projectId);
}
