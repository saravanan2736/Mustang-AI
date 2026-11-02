/**
 * Backend connection: where the API lives and which session token to send.
 *
 * Desktop (Tauri): the shell owns the backend process. It picks the port, generates the
 * token and reports lifecycle changes through the `backend://state` event; the address
 * and token are read with the `backend_info` command.
 * Browser development: the backend runs at a fixed address and the token comes from the
 * origin-checked `/api/auth/bootstrap` endpoint.
 */
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export type BackendStatus =
  | { state: "starting"; detail: string }
  | { state: "ready"; base_url: string; pid: number }
  | { state: "restarting"; attempt: number; reason: string }
  | { state: "failed"; reason: string; log_tail: string[] }
  | { state: "stopping" }
  | { state: "stopped" };

type BackendInfo = BackendStatus & {
  token: string | null;
  runtime_source: string | null;
  data_root: string | null;
  log_dir: string | null;
};

export const TOKEN_HEADER = "X-Mustang-Token";
export const isDesktop = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

const DEV_BACKEND_URL: string = import.meta.env.VITE_BACKEND_URL ?? "http://127.0.0.1:18000";

let baseUrl = DEV_BACKEND_URL;
let token: string | null = null;
let logDir: string | null = null;
let status: BackendStatus = { state: "starting", detail: "Connecting to the local engine" };
const listeners = new Set<(s: BackendStatus) => void>();

export function backendUrl(): string {
  return baseUrl;
}

export function sessionToken(): string | null {
  return token;
}

export function backendLogDir(): string | null {
  return logDir;
}

export function getBackendStatus(): BackendStatus {
  return status;
}

export function subscribeBackendStatus(fn: (s: BackendStatus) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setStatus(next: BackendStatus) {
  status = next;
  listeners.forEach((fn) => fn(next));
}

async function refreshFromShell(): Promise<void> {
  const info = await invoke<BackendInfo>("backend_info");
  logDir = info.log_dir;
  if (info.state === "ready" && info.token) {
    baseUrl = info.base_url;
    token = info.token;
  }
  const { token: _t, runtime_source: _r, data_root: _d, log_dir: _l, ...state } = info;
  setStatus(state as BackendStatus);
}

async function browserBootstrap(): Promise<void> {
  try {
    const res = await fetch(`${baseUrl}/api/auth/bootstrap`);
    if (res.ok) {
      token = (await res.json()).token;
    } else {
      console.warn(`Bootstrap auth refused (${res.status}); API calls will be rejected.`);
    }
  } catch (err) {
    console.warn("Backend unreachable during bootstrap:", err);
  }
  // The browser UI renders regardless and surfaces API errors itself, as before.
  setStatus({ state: "ready", base_url: baseUrl, pid: 0 });
}

let initialized: Promise<void> | null = null;

/** Resolve the backend address and token once; safe to call repeatedly. */
export function initBackendConnection(): Promise<void> {
  if (!initialized) {
    initialized = (async () => {
      if (!isDesktop) {
        await browserBootstrap();
        return;
      }
      // Subscribe first so no transition between the two calls is missed.
      await listen<BackendStatus>("backend://state", (event) => {
        if (event.payload.state === "ready") {
          void refreshFromShell();
        } else {
          setStatus(event.payload);
        }
      });
      await refreshFromShell();
    })();
  }
  return initialized;
}

/** Ask the shell to restart the backend (desktop), or re-run bootstrap (browser). */
export async function retryBackend(): Promise<void> {
  if (isDesktop) {
    await invoke("backend_restart");
  } else {
    await browserBootstrap();
  }
}
