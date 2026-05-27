const rawApiBaseUrl =
  process.env.EXPO_PUBLIC_AISOFLOW_API_URL ??
  process.env.EXPO_PUBLIC_AGENTDOCK_API_URL ??
  process.env.EXPO_PUBLIC_AGENTDOCK_APP_URL ??
  "https://api.aisosu.fi";

export const API_BASE_URL = rawApiBaseUrl.replace(/\/$/, "");
