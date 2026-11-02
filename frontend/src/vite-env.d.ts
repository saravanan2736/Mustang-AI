/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Browser development only: backend address (default http://127.0.0.1:18000). */
  readonly VITE_BACKEND_URL?: string;
}
