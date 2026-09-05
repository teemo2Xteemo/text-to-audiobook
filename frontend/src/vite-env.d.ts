/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEFAULT_SOURCE_LANGUAGE?: string;
  readonly VITE_DEFAULT_TARGET_LANGUAGE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
