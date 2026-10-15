import { apiClient } from "../lib/api/client";
import { ServiceStatus } from "./service-status";

export const dynamic = "force-dynamic";

export default async function Home() {
  let health = null;
  let apiRoot = null;
  let error: string | null = null;

  try {
    const [healthResponse, rootResponse] = await Promise.all([
      apiClient.GET("/healthz"),
      apiClient.GET("/api/v1"),
    ]);
    if (healthResponse.error || rootResponse.error) {
      error = "API 返回了异常响应";
    } else {
      health = healthResponse.data;
      apiRoot = rootResponse.data;
    }
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <main>
      <h1>Novel LLM 工作台</h1>
      <p>单作者长篇小说创作工作台 · 仅监听 127.0.0.1</p>
      <ServiceStatus health={health} apiRoot={apiRoot} error={error} />
    </main>
  );
}
