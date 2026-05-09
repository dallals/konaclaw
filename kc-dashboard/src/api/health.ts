import { apiGet } from "./client";

export type Health = { status: string; uptime_s: number; agents: number };
export const getHealth = () => apiGet<Health>("/health");
