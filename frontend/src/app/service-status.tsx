import type { components } from "../lib/api/schema";

type HealthResponse = components["schemas"]["HealthResponse"];
type APIRoot = components["schemas"]["APIRoot"];

export interface ServiceStatusProps {
  health: HealthResponse | null;
  apiRoot: APIRoot | null;
  error: string | null;
}

export function ServiceStatus({ health, apiRoot, error }: ServiceStatusProps) {
  const alive = health?.status === "ok";
  return (
    <section aria-label="服务状态">
      <h2>服务状态</h2>
      {error !== null ? (
        <p role="alert">
          无法连接 API：<code>{error}</code>
        </p>
      ) : alive ? (
        <p data-testid="api-status">API 状态：正常</p>
      ) : (
        <p role="alert" data-testid="api-status">
          API 状态：异常
        </p>
      )}
      {apiRoot !== null && (
        <p data-testid="api-version">
          API 版本：{apiRoot.name} {apiRoot.version}
        </p>
      )}
    </section>
  );
}
