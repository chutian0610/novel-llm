/**
 * Shared problem-details presentation (ADR-0008): every server action maps
 * the API's stable error codes to the workbench's Simplified Chinese labels.
 */

export type ApiError = { code?: string; detail?: string; title?: string };

const ERROR_LABELS: Record<string, string> = {
  idempotency_key_conflict: "幂等键已被不同的请求使用,请重试",
  project_archived: "项目已归档",
  project_not_found: "项目不存在",
  volume_not_found: "卷不存在",
  chapter_not_found: "章节不存在",
  outline_not_found: "尚未发布 Outline",
  structure_order_invalid: "重排顺序与当前结构不符",
  invalid_outline_payload: "Outline 载荷不符合计划结构",
  etag_mismatch: "版本已变化,请刷新后重试",
  missing_if_match: "缺少 If-Match 头",
  malformed_if_match: "If-Match 头格式不正确",
  invalid_cursor: "无效的分页游标",
  validation_error: "请求校验失败",
};

export function problemMessage(error: unknown): string {
  const body = (error ?? {}) as ApiError;
  const label = body.code ? (ERROR_LABELS[body.code] ?? body.title ?? body.code) : "请求失败";
  return body.detail ? `${label}(${body.detail})` : label;
}
