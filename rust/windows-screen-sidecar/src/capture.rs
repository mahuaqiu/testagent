use image::codecs::jpeg::JpegEncoder;
use image::{ColorType, ImageEncoder};
use std::mem::size_of;
use windows::core::Error as WinError;
use windows::Win32::Foundation::{BOOL, HWND, LPARAM, RECT};
use windows::Win32::Graphics::Gdi::{HDC, HGDIOBJ, HMONITOR, *};

#[derive(Clone, Debug)]
pub struct MonitorRect {
    pub left: i32,
    pub top: i32,
    pub width: i32,
    pub height: i32,
}

#[derive(Clone, Debug)]
pub struct CapturedFrame {
    pub width: u32,
    pub height: u32,
    pub bgra: Vec<u8>,
    pub captured_at_ms: u128,
}

fn win_err(message: &str) -> String {
    format!("{message}: {}", WinError::from_win32())
}

unsafe extern "system" fn enum_display_monitors_proc(
    hmonitor: HMONITOR,
    _hdc: HDC,
    _rc: *mut RECT,
    lparam: LPARAM,
) -> BOOL {
    let monitors = &mut *(lparam.0 as *mut Vec<MonitorRect>);
    let mut info = MONITORINFO::default();
    info.cbSize = size_of::<MONITORINFO>() as u32;
    if GetMonitorInfoW(hmonitor, &mut info).as_bool() {
        let rc = info.rcMonitor;
        monitors.push(MonitorRect {
            left: rc.left,
            top: rc.top,
            width: rc.right - rc.left,
            height: rc.bottom - rc.top,
        });
    }
    true.into()
}

pub fn list_monitors() -> Result<Vec<MonitorRect>, String> {
    let mut monitors: Vec<MonitorRect> = Vec::new();
    unsafe {
        EnumDisplayMonitors(
            HDC(std::ptr::null_mut()),
            None,
            Some(enum_display_monitors_proc),
            LPARAM(&mut monitors as *mut _ as isize),
        )
        .ok()
        .map_err(|e| format!("EnumDisplayMonitors failed: {e}"))?;
    }

    if monitors.is_empty() {
        return Err("no monitor found".to_string());
    }

    monitors.sort_by(|a, b| {
        let a_primary = a.left == 0;
        let b_primary = b.left == 0;
        a_primary
            .cmp(&b_primary)
            .then(a.left.cmp(&b.left))
            .then(a.top.cmp(&b.top))
    });

    Ok(monitors)
}

pub fn get_monitor_rect(monitor: u32) -> Result<MonitorRect, String> {
    let monitors = list_monitors()?;

    // 统一逻辑：monitor=1 找 left=0 的显示器（主屏幕），monitor=2 找 left!=0 的显示器（副屏幕）
    // 这与 d3d11.rs 的 detect_monitor 逻辑保持一致
    let target = match monitor {
        1 => monitors.iter().find(|m| m.left == 0),
        2 => monitors.iter().find(|m| m.left != 0),
        _ => monitors.get((monitor as usize).saturating_sub(1)),
    };

    target
        .cloned()
        .ok_or_else(|| format!("monitor not found: {monitor}"))
}

pub fn capture_monitor(monitor: u32) -> Result<CapturedFrame, String> {
    let rect = get_monitor_rect(monitor)?;
    // 直接捕获实际尺寸，不再做 8 像素对齐
    // 编码器内部会自动处理对齐（pad_bgra_to_aligned）
    capture_rect(&rect)
}

pub fn capture_rect(rect: &MonitorRect) -> Result<CapturedFrame, String> {
        unsafe {
        // 保持原始尺寸，编码器会处理对齐
        let screen_dc = GetDC(HWND(std::ptr::null_mut()));
        if screen_dc.0.is_null() {
            return Err(win_err("GetDC failed"));
        }

        let mem_dc = CreateCompatibleDC(screen_dc);
        if mem_dc.0.is_null() {
            let _ = ReleaseDC(HWND(std::ptr::null_mut()), screen_dc);
            return Err(win_err("CreateCompatibleDC failed"));
        }

        let mut bmi = BITMAPINFO::default();
        bmi.bmiHeader.biSize = size_of::<BITMAPINFOHEADER>() as u32;
        bmi.bmiHeader.biWidth = rect.width;
        bmi.bmiHeader.biHeight = -rect.height;
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;
        bmi.bmiHeader.biCompression = BI_RGB.0;

        let mut bits: *mut std::ffi::c_void = std::ptr::null_mut();
        let hbitmap = CreateDIBSection(screen_dc, &bmi, DIB_RGB_COLORS, &mut bits, None, 0)
            .map_err(|e| format!("CreateDIBSection failed: {e}"))?;
        if hbitmap.0.is_null() {
            let _ = DeleteDC(mem_dc);
            let _ = ReleaseDC(HWND(std::ptr::null_mut()), screen_dc);
            return Err(win_err("CreateDIBSection failed"));
        }

        let old_obj = SelectObject(mem_dc, HGDIOBJ(hbitmap.0));
        if old_obj.0.is_null() {
            let _ = DeleteObject(HGDIOBJ(hbitmap.0));
            let _ = DeleteDC(mem_dc);
            let _ = ReleaseDC(HWND(std::ptr::null_mut()), screen_dc);
            return Err(win_err("SelectObject failed"));
        }

        BitBlt(
            mem_dc,
            0,
            0,
            rect.width,
            rect.height,
            screen_dc,
            rect.left,
            rect.top,
            SRCCOPY | CAPTUREBLT,
        )
        .map_err(|e| format!("BitBlt failed: {e}"))?;

        let bytes_len = (rect.width as u32 as usize)
            .saturating_mul(rect.height as u32 as usize)
            .saturating_mul(4);
        let bgra = std::slice::from_raw_parts(bits as *const u8, bytes_len).to_vec();

        let _ = SelectObject(mem_dc, old_obj);
        let _ = DeleteObject(HGDIOBJ(hbitmap.0));
        let _ = DeleteDC(mem_dc);
        let _ = ReleaseDC(HWND(std::ptr::null_mut()), screen_dc);

        Ok(CapturedFrame {
            width: rect.width as u32,
            height: rect.height as u32,
            bgra,
            captured_at_ms: current_timestamp_ms(),
        })
    }
}

pub fn bgra_to_jpeg(bgra: &[u8], width: u32, height: u32, quality: u8) -> Result<Vec<u8>, String> {
    let expected_len = (width as usize)
        .saturating_mul(height as usize)
        .saturating_mul(4);
    if bgra.len() != expected_len {
        return Err(format!(
            "frame size mismatch: expected {expected_len} bytes, got {}",
            bgra.len()
        ));
    }

    let mut rgb = Vec::with_capacity((width as usize).saturating_mul(height as usize).saturating_mul(3));
    for chunk in bgra.chunks_exact(4) {
        rgb.push(chunk[2]);
        rgb.push(chunk[1]);
        rgb.push(chunk[0]);
    }

    let mut output = Vec::new();
    let encoder = JpegEncoder::new_with_quality(&mut output, quality);
    encoder
        .write_image(&rgb, width, height, ColorType::Rgb8.into())
        .map_err(|e| format!("jpeg encode failed: {e}"))?;
    Ok(output)
}

pub fn current_timestamp_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

