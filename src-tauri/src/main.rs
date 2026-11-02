// Release builds use the Windows GUI subsystem: no console window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    mustang_desktop_lib::run();
}
