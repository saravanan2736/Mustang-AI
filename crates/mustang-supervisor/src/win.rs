//! Thin wrappers over the Win32 Job Object and process APIs.

use std::io;
use std::mem::{size_of, zeroed};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::process::{Child, Command};
use std::ptr::null;

use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob, JobObjectBasicAccountingInformation,
    JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
    TerminateJobObject, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{
    GetExitCodeProcess, OpenProcess, OpenThread, ResumeThread, PROCESS_QUERY_LIMITED_INFORMATION,
    THREAD_SUSPEND_RESUME,
};

pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const CREATE_SUSPENDED: u32 = 0x0000_0004;
const STILL_ACTIVE: u32 = 259;

pub struct JobObject {
    handle: HANDLE,
}

// The job handle is a kernel handle; the API is thread-safe.
unsafe impl Send for JobObject {}
unsafe impl Sync for JobObject {}

impl JobObject {
    /// Anonymous job: every process in it is killed when the last handle closes.
    pub fn new_kill_on_close() -> io::Result<Self> {
        unsafe {
            let handle = CreateJobObjectW(null(), null());
            if handle.is_null() {
                return Err(io::Error::last_os_error());
            }
            let job = Self { handle };
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
            info.BasicLimitInformation.LimitFlags =
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION;
            let ok = SetInformationJobObject(
                job.handle,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(job)
        }
    }

    pub fn assign(&self, child: &Child) -> io::Result<()> {
        let ok = unsafe { AssignProcessToJobObject(self.handle, child.as_raw_handle() as HANDLE) };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    pub fn terminate(&self, exit_code: u32) -> io::Result<()> {
        let ok = unsafe { TerminateJobObject(self.handle, exit_code) };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    pub fn active_processes(&self) -> io::Result<u32> {
        unsafe {
            let mut info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = zeroed();
            let ok = QueryInformationJobObject(
                self.handle,
                JobObjectBasicAccountingInformation,
                &mut info as *mut _ as *mut _,
                size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                std::ptr::null_mut(),
            );
            if ok == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(info.ActiveProcesses)
        }
    }

    pub fn contains_pid(&self, pid: u32) -> io::Result<bool> {
        unsafe {
            let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if h.is_null() {
                return Err(io::Error::last_os_error());
            }
            let mut result = 0;
            let ok = IsProcessInJob(h, self.handle, &mut result);
            CloseHandle(h);
            if ok == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(result != 0)
        }
    }
}

impl Drop for JobObject {
    fn drop(&mut self) {
        unsafe { CloseHandle(self.handle) };
    }
}

/// Spawn suspended, assign to the job, then resume: no window in which the child
/// can create a grandchild outside the job.
pub fn spawn_in_job(cmd: &mut Command, job: &JobObject) -> io::Result<Child> {
    cmd.creation_flags(CREATE_SUSPENDED | CREATE_NO_WINDOW);
    let mut child = cmd.spawn()?;
    if let Err(e) = job.assign(&child) {
        let _ = child.kill();
        return Err(e);
    }
    if let Err(e) = resume_process_threads(child.id()) {
        let _ = child.kill();
        return Err(e);
    }
    Ok(child)
}

fn resume_process_threads(pid: u32) -> io::Result<()> {
    unsafe {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
        if snap == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        let mut entry: THREADENTRY32 = zeroed();
        entry.dwSize = size_of::<THREADENTRY32>() as u32;
        let mut resumed = 0;
        let mut more = Thread32First(snap, &mut entry) != 0;
        while more {
            if entry.th32OwnerProcessID == pid {
                let th = OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID);
                if !th.is_null() {
                    if ResumeThread(th) != u32::MAX {
                        resumed += 1;
                    }
                    CloseHandle(th);
                }
            }
            more = Thread32Next(snap, &mut entry) != 0;
        }
        CloseHandle(snap);
        if resumed == 0 {
            return Err(io::Error::new(io::ErrorKind::Other, "no suspended thread found to resume"));
        }
        Ok(())
    }
}

/// True if `pid` refers to a process that has not exited.
pub fn process_alive(pid: u32) -> bool {
    unsafe {
        let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if h.is_null() {
            return false;
        }
        let mut code = 0u32;
        let ok = GetExitCodeProcess(h, &mut code);
        CloseHandle(h);
        ok != 0 && code == STILL_ACTIVE
    }
}
