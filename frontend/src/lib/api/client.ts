import createClient from "openapi-fetch";
import type { paths } from "./schema";

/**
 * The workbench only ever talks to the API from the server side: the Next.js
 * process reaches the API over the internal Docker network, and only the web
 * interface itself is published to 127.0.0.1 (ADR-0005).
 */
export const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

export const apiClient = createClient<paths>({ baseUrl: API_BASE_URL });
