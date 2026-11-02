//! Windows logoff / shutdown / restart.
//!
//! Windows sends WM_QUERYENDSESSION then WM_ENDSESSION to top-level windows and terminates
//! the process soon after WM_ENDSESSION returns, so RunEvent::Exit never runs. The main
//! window is subclassed to run the graceful backend shutdown (persist partial chat output,
//! pause downloads, unload the model) inside WM_ENDSESSION, with a shutdown-block reason so
//! Windows shows why Mustang AI needs a moment.

use tauri::WebviewWindow;
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows_sys::Win32::System::Shutdown::{ShutdownBlockReasonCreate, ShutdownBlockReasonDestroy};
use windows_sys::Win32::UI::Shell::{DefSubclassProc, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::{WM_ENDSESSION, WM_QUERYENDSESSION};

const SUBCLASS_ID: usize = 0x4d55_5354; // "MUST"
const BLOCK_REASON: &str = "Mustang AI is saving your conversations and stopping the local engine…";

type OnEnd = Box<dyn Fn() + Send + Sync>;

/// Install the handler on the main window. Must be called on the UI thread (Tauri setup).
pub fn install(window: &WebviewWindow, on_session_end: OnEnd) -> Result<(), String> {
    let hwnd = window.hwnd().map_err(|e| e.to_string())?.0 as HWND;
    // Leaked on purpose: lives as long as the window (the process).
    let data = Box::into_raw(Box::new(on_session_end)) as usize;
    let ok = unsafe { SetWindowSubclass(hwnd, Some(subclass_proc), SUBCLASS_ID, data) };
    if ok == 0 {
        return Err("SetWindowSubclass failed".into());
    }
    Ok(())
}

unsafe extern "system" fn subclass_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    data: usize,
) -> LRESULT {
    match msg {
        // Never veto the user's logoff or shutdown.
        WM_QUERYENDSESSION => 1,
        WM_ENDSESSION if wparam != 0 => {
            let reason: Vec<u16> = BLOCK_REASON.encode_utf16().chain(std::iter::once(0)).collect();
            ShutdownBlockReasonCreate(hwnd, reason.as_ptr());
            let on_end = &*(data as *const OnEnd);
            on_end();
            ShutdownBlockReasonDestroy(hwnd);
            0
        }
        _ => DefSubclassProc(hwnd, msg, wparam, lparam),
    }
}
