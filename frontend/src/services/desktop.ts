/**
 * Desktop-only shell commands (Tauri). Every call is validated in Rust; the webview has no
 * filesystem or shell access of its own.
 */
import { invoke } from "@tauri-apps/api/core";
import { isDesktop } from "./connection";

export interface DesktopSettings {
  models_dir: string | null;
  models_dir_is_default: boolean;
  close_to_tray: boolean;
  data_root: string | null;
  log_dir: string | null;
}

export const getDesktopSettings = () => invoke<DesktopSettings>("desktop_settings");
export const setCloseToTray = (enabled: boolean) => invoke<void>("set_close_to_tray", { enabled });
/** Opens the native folder picker; resolves to the new folder, or null if cancelled. */
export const chooseModelsFolder = () => invoke<string | null>("choose_models_folder");
export const resetModelsFolder = () => invoke<string>("reset_models_folder");
export const openFolder = (which: "models" | "logs") => invoke<void>("open_folder", { which });
export const openExternal = (url: string) => invoke<void>("open_external", { url });

/**
 * In the desktop app, links that leave the app (target="_blank" or another origin) open in
 * the user's default browser via the shell's allow-list instead of inside the app window.
 */
export function installExternalLinkHandler(): void {
  if (!isDesktop) return;
  document.addEventListener(
    "click",
    (event) => {
      const anchor = (event.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!anchor) return;
      const url = new URL(anchor.href, location.href);
      if (anchor.target !== "_blank" && url.origin === location.origin) return;
      event.preventDefault();
      openExternal(url.href).catch((err) => console.warn("Link not opened:", err));
    },
    true
  );
}
