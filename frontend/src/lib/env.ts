import { AUTO_SOURCE_LANGUAGE } from "./constants";

export function apiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL?.trim() ?? "";
}

export function envDefaultSourceLanguage(): string {
  const value = import.meta.env.VITE_DEFAULT_SOURCE_LANGUAGE?.trim();
  return value || AUTO_SOURCE_LANGUAGE;
}

export function envDefaultTargetLanguage(): string | undefined {
  const value = import.meta.env.VITE_DEFAULT_TARGET_LANGUAGE?.trim();
  return value || undefined;
}
