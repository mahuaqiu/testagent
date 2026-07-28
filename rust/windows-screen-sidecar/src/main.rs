mod capture;
mod protocol;
mod session;
mod win_recorder;
mod media;

use crate::capture::current_timestamp_ms;
use crate::protocol::{Request, Response};
use crate::session::SessionHandle;
use serde_json::json;
use std::collections::HashMap;
use std::io::{self, BufRead, Write};
use std::sync::{Arc, Mutex};

struct AppState {
    sessions: HashMap<String, SessionHandle>,
}

impl AppState {
    fn new() -> Self {
        Self {
            sessions: HashMap::new(),
        }
    }

    fn get_or_create_session(
        &mut self,
        session_id: String,
        monitor: u32,
        idle_fps: u32,
        active_fps: u32,
    ) -> Result<&SessionHandle, String> {
        if !self.sessions.contains_key(&session_id) {
            let session = SessionHandle::new(session_id.clone(), monitor, idle_fps, active_fps)?;
            self.sessions.insert(session_id.clone(), session);
        }
        Ok(self.sessions.get(&session_id).expect("session inserted"))
    }

    fn remove_session(&mut self, session_id: &str) -> Option<SessionHandle> {
        self.sessions.remove(session_id)
    }
}

fn parse_string(value: &serde_json::Value, key: &str, default: &str) -> String {
    value
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or(default)
        .to_string()
}

fn parse_u32(value: &serde_json::Value, key: &str, default: u32) -> u32 {
    value
        .get(key)
        .and_then(|v| v.as_u64())
        .map(|v| v as u32)
        .unwrap_or(default)
}

fn parse_u128(value: &serde_json::Value, key: &str, default: u128) -> u128 {
    value
        .get(key)
        .and_then(|v| v.as_u64())
        .map(|v| v as u128)
        .unwrap_or(default)
}

fn parse_bool(value: &serde_json::Value, key: &str, default: bool) -> bool {
    value.get(key).and_then(|v| v.as_bool()).unwrap_or(default)
}

fn handle_request(state: &Arc<Mutex<AppState>>, request: Request) -> Response {
    let params = request.params;
    let cmd = request.cmd.as_str();

    match cmd {
        "health" => Response::ok(
            request.id,
            json!({
                "status": "ok",
                "timestamp_ms": current_timestamp_ms(),
                "sessions": state.lock().map(|s| s.sessions.len()).unwrap_or(0),
            }),
        ),
        "get_monitors" => {
            match crate::capture::list_monitors() {
                Ok(monitors) => {
                    let monitors_json: Vec<serde_json::Value> = monitors
                        .iter()
                        .enumerate()
                        .map(|(i, m)| {
                            json!({
                                "index": i + 1,
                                "left": m.left,
                                "top": m.top,
                                "width": m.width,
                                "height": m.height,
                            })
                        })
                        .collect();
                    Response::ok(request.id, json!({ "monitors": monitors_json }))
                }
                Err(e) => Response::err(request.id, e),
            }
        }
        "session_open" => {
            let session_id = parse_string(&params, "session_id", "windows/1");
            let monitor = parse_u32(&params, "monitor", 1);
            let idle_fps = parse_u32(&params, "idle_fps", 1);
            let active_fps = parse_u32(&params, "active_fps", 15);
            let mut guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => return Response::err(request.id, "state mutex poisoned"),
            };
            match guard.get_or_create_session(session_id.clone(), monitor, idle_fps, active_fps) {
                Ok(session) => Response::ok(
                    request.id,
                    json!({
                        "session_id": session_id,
                        "monitor": session.monitor().unwrap_or(monitor),
                    }),
                ),
                Err(err) => Response::err(request.id, err),
            }
        }
        "snapshot" => {
            let session_id = parse_string(&params, "session_id", "windows/1");
            let format = parse_string(&params, "format", "jpeg");
            let quality = parse_u32(&params, "quality", 80) as u8;
            let max_age_ms = parse_u128(&params, "max_age_ms", 100);
            let guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => return Response::err(request.id, "state mutex poisoned"),
            };
            let session = match guard.sessions.get(&session_id) {
                Some(session) => session,
                None => return Response::err(request.id, format!("session not found: {session_id}")),
            };
            match session.snapshot(&format, quality, max_age_ms) {
                Ok(data) => Response::ok(request.id, data),
                Err(err) => Response::err(request.id, err),
            }
        }
        "recording_start" => {
            let session_id = parse_string(&params, "session_id", "windows/1");
            let output_path = parse_string(&params, "output_path", "");
            let fps = parse_u32(&params, "fps", 10);
            let audio = params.get("audio").and_then(|v| v.as_bool()).unwrap_or(false);
            let watermark = params.get("watermark").and_then(|v| v.as_bool()).unwrap_or(true);
            let guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => return Response::err(request.id, "state mutex poisoned"),
            };
            let session = match guard.sessions.get(&session_id) {
                Some(session) => session,
                None => return Response::err(request.id, format!("session not found: {session_id}")),
            };
            match session.start_recording(output_path, fps, audio, watermark) {
                Ok(data) => Response::ok(request.id, data),
                Err(err) => Response::err(request.id, err),
            }
        }
        "recording_stop" => {
            let session_id = parse_string(&params, "session_id", "windows/1");
            let guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => return Response::err(request.id, "state mutex poisoned"),
            };
            let session = match guard.sessions.get(&session_id) {
                Some(session) => session,
                None => return Response::err(request.id, format!("session not found: {session_id}")),
            };
            match session.stop_recording() {
                Ok(data) => Response::ok(request.id, data),
                Err(err) => Response::err(request.id, err),
            }
        }
        "stream_start" => {
            let session_id = parse_string(&params, "session_id", "windows/1");
            let fps = parse_u32(&params, "fps", 10);
            let bitrate = parse_u32(&params, "bitrate", 2_000_000);
            let profile = parse_u32(&params, "profile", 66);
            let binary = parse_bool(&params, "binary", false);

            eprintln!("[windows-sidecar] stream_start: session_id={}, fps={}", session_id, fps);

            let guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => return Response::err(request.id, "state mutex poisoned"),
            };
            let session = match guard.sessions.get(&session_id) {
                Some(session) => session,
                None => return Response::err(request.id, format!("session not found: {session_id}")),
            };
            let result = session.start_streaming(fps, bitrate, profile, binary);
            eprintln!("[windows-sidecar] stream_start: result ready, generating response");
            // 强制刷新 stderr
            use std::io::Write;
            std::io::stderr().flush().ok();
            match result {
                Ok(data) => {
                    eprintln!("[windows-sidecar] stream_start: sending ok response");
                    Response::ok(request.id, data)
                }
                Err(err) => {
                    eprintln!("[windows-sidecar] stream_start: sending error response: {}", err);
                    Response::err(request.id, err)
                }
            }
        }
        "stream_next" => {
            let session_id = parse_string(&params, "session_id", "windows/1");
            let guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => return Response::err(request.id, "state mutex poisoned"),
            };
            let session = match guard.sessions.get(&session_id) {
                Some(session) => session,
                None => return Response::err(request.id, format!("session not found: {session_id}")),
            };
            match session.next_stream_frame() {
                Ok(data) => Response::ok(request.id, data),
                Err(err) => Response::err(request.id, err),
            }
        }
        "stream_stop" => {
            let session_id = parse_string(&params, "session_id", "windows/1");
            let guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => return Response::err(request.id, "state mutex poisoned"),
            };
            let session = match guard.sessions.get(&session_id) {
                Some(session) => session,
                None => return Response::err(request.id, format!("session not found: {session_id}")),
            };
            match session.stop_streaming() {
                Ok(data) => Response::ok(request.id, data),
                Err(err) => Response::err(request.id, err),
            }
        }
"stream_push_start" => {
            let session_id = parse_string(&params, "session_id", "windows/1");
            let fps = parse_u32(&params, "fps", 20);

            eprintln!("[windows-sidecar] stream_push_start: session_id={}, fps={}", session_id, fps);

            // 强制重启 capture_loop（因为即使线程还在，也可能很快退出）
            let session_id_clone = session_id.clone();
            {
                let mut guard = match state.lock() {
                    Ok(guard) => guard,
                    Err(_) => return Response::err(request.id, "state mutex poisoned"),
                };
                if let Some(session_handle) = guard.sessions.get_mut(&session_id) {
                    let _ = session_handle.set_push_enabled(true);
                    let _ = session_handle.set_push_fps(fps);
                    eprintln!("[windows-sidecar] push_enabled set to true");
                } else {
                    eprintln!("[windows-sidecar] session not found: {}", session_id);
                }
            }

            // 总是重启 capture_loop，确保它运行
            if let Ok(mut guard) = state.lock() {
                if let Some(session_handle) = guard.sessions.get_mut(&session_id_clone) {
                    match session_handle.ensure_capture_thread_running() {
                        Ok(()) => eprintln!("[windows-sidecar] stream_push_start: capture_loop restarted"),
                        Err(e) => eprintln!("[windows-sidecar] stream_push_start: restart failed: {}", e),
                    }
                    // 主动推送一次 SPS/PPS，确保 Python 等待 SPS+PPS 能可靠拿到
                    if let Err(e) = session_handle.push_sps_pps_once() {
                        eprintln!("[windows-sidecar] stream_push_start: push_sps_pps_once failed: {}", e);
                    } else {
                        eprintln!("[windows-sidecar] stream_push_start: SPS/PPS pushed");
                    }
                }
            }

            Response::ok(request.id, serde_json::json!({"status": "push_started"}))
        }        "stream_push_stop" => {
            let session_id = parse_string(&params, "session_id", "windows/1");

            let mut guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => return Response::err(request.id, "state mutex poisoned"),
            };
            if let Some(session_handle) = guard.sessions.get_mut(&session_id) {
                let _ = session_handle.set_push_enabled(false);
            }

            Response::ok(request.id, serde_json::json!({"status": "push_stopped"}))
        }
        "session_close" => {
            let session_id = parse_string(&params, "session_id", "windows/1");
            let session = {
                let mut guard = match state.lock() {
                    Ok(guard) => guard,
                    Err(_) => return Response::err(request.id, "state mutex poisoned"),
                };
                guard.remove_session(&session_id)
            };
            match session {
                Some(session) => match session.close() {
                    Ok(data) => Response::ok(request.id, data),
                    Err(err) => Response::err(request.id, err),
                },
                None => Response::err(request.id, format!("session not found: {session_id}")),
            }
        }
        "shutdown" => {
            let sessions = {
                let mut guard = match state.lock() {
                    Ok(guard) => guard,
                    Err(_) => return Response::err(request.id, "state mutex poisoned"),
                };
                guard.sessions.drain().map(|(_, s)| s).collect::<Vec<_>>()
            };
            for session in sessions {
                let _ = session.close();
            }
            Response::empty_ok(request.id)
        }
        _ => Response::err(request.id, format!("unknown command: {cmd}")),
    }
}

fn main() {
    // 将系统定时器精度提到 1ms：抓帧/录制节拍线程都依赖 thread::sleep，
    // 默认 ~15.6ms 精度会把 20fps 的 50ms 节拍拉长到 55~65ms，
    // 导致录制 tick 复用旧帧（水印冻结后跳变）。进程退出时系统自动恢复。
    unsafe {
        let _ = windows::Win32::Media::timeBeginPeriod(1);
    }

    // 设置 panic 处理器，将 panic 信息输出到 stderr
    std::panic::set_hook(Box::new(|panic_info| {
        let msg = if let Some(s) = panic_info.payload().downcast_ref::<&str>() {
            s.to_string()
        } else if let Some(s) = panic_info.payload().downcast_ref::<String>() {
            s.clone()
        } else {
            "Unknown panic".to_string()
        };
        let location = if let Some(loc) = panic_info.location() {
            format!("{}:{}:{}", loc.file(), loc.line(), loc.column())
        } else {
            "unknown location".to_string()
        };
        eprintln!("[windows-sidecar] PANIC: {} at {}", msg, location);
    }));

    let state = Arc::new(Mutex::new(AppState::new()));
    eprintln!("[windows-screen-sidecar] started");

    let stdin = io::stdin();
    let mut stdout = io::stdout();

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(line) => line,
            Err(err) => {
                eprintln!("[windows-screen-sidecar] stdin error: {err}");
                break;
            }
        };

        if line.trim().is_empty() {
            continue;
        }

        // 处理控制命令（以 @ 开头）
        if line.starts_with('@') {
            let cmd = line.trim();
            let mut guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => continue,
            };
            if cmd == "@PUSH_STOP" {
                // 停止所有会话的推模式
                for (_, session_handle) in guard.sessions.iter_mut() {
                    let _ = session_handle.set_push_enabled(false);
                }
                eprintln!("[windows-screen-sidecar] push mode stopped");
            } else if cmd.starts_with("@FPS=") {
                // 设置帧率
                if let Some(fps_str) = cmd.strip_prefix("@FPS=") {
                    if let Ok(fps) = fps_str.parse::<u32>() {
                        for (_, session_handle) in guard.sessions.iter_mut() {
                            let _ = session_handle.set_push_fps(fps);
                        }
                        eprintln!("[windows-screen-sidecar] push fps set to {}", fps);
                    }
                }
            }
            continue;
        }

        let request = match serde_json::from_str::<Request>(&line) {
            Ok(request) => request,
            Err(err) => {
                let response = Response::err(0, format!("invalid request: {err}"));
                let _ = writeln!(stdout, "{}", serde_json::to_string(&response).unwrap());
                let _ = stdout.flush();
                continue;
            }
        };

        let should_shutdown = request.cmd == "shutdown";
        let response = handle_request(&state, request);
        let payload = serde_json::to_string(&response).unwrap_or_else(|err| {
            serde_json::to_string(&Response::err(response.id, format!("serialize error: {err}")))
                .unwrap()
        });
        if writeln!(stdout, "{payload}").is_err() {
            eprintln!("[windows-screen-sidecar] stdout write failed");
            break;
        }
        if stdout.flush().is_err() {
            eprintln!("[windows-screen-sidecar] stdout flush failed");
            break;
        }

        if should_shutdown {
            break;
        }
    }

    eprintln!("[windows-screen-sidecar] stopped");
}
