mod capture;
mod protocol;
mod session;
mod win_recorder;

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
            let guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => return Response::err(request.id, "state mutex poisoned"),
            };
            let session = match guard.sessions.get(&session_id) {
                Some(session) => session,
                None => return Response::err(request.id, format!("session not found: {session_id}")),
            };
            match session.start_streaming(fps, bitrate, profile) {
                Ok(data) => Response::ok(request.id, data),
                Err(err) => Response::err(request.id, err),
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
