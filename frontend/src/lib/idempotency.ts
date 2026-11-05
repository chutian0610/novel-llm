/**
 * 客户端为一次创建意图生成幂等键(ADR-0008):失败重试必须复用同一键,
 * 重放才返回原项目而非复制;成功之后为新的一轮创建换键。
 */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `key-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
