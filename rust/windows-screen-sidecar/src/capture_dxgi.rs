//! DXGI Desktop Duplication 抓屏实现（录制/推流热路径的主抓帧源）。
//!
//! 相比 GDI BitBlt+CAPTUREBLT（走 DWM 合成器回读，偶发单帧阻塞 100ms+），
//! Desktop Duplication 由 DWM 合成后主动推帧，AcquireNextFrame 拿到的是
//! GPU 显存纹理引用，拷回内存走优化路径，单帧成本稳定在几 ms。
//!
//! GDI 仅作为自动降级路径保留：RDP 会话、竖屏旋转、驱动异常等场景
//! Duplication 不可用时静默回退，不影响录制链路存活。
//!
//! 注意：本模块运行在 capture-producer 线程热路径上，且推流模式下 stderr
//! 承载二进制帧流，因此这里不做任何 stderr 日志输出，失败以 Err 上抛。

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use windows::core::Interface;
use windows::Win32::Graphics::Direct3D::D3D_DRIVER_TYPE_UNKNOWN;
use windows::Win32::Graphics::Direct3D11::*;
use windows::Win32::Graphics::Dxgi::Common::{
    DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_MODE_ROTATION_IDENTITY, DXGI_MODE_ROTATION_UNSPECIFIED,
    DXGI_SAMPLE_DESC,
};
use windows::Win32::Graphics::Dxgi::*;

use crate::capture::{capture_monitor, current_timestamp_ms, CapturedFrame};

/// AcquireNextFrame 等待新帧的预算：静止画面下超时复用上一帧像素，
/// 预算取远小于最小录制拍（10fps=100ms）的值，超时部分由抓帧线程的
/// 绝对节拍吸收，不会拖慢实际抓帧率。
const ACQUIRE_TIMEOUT_MS: u32 = 8;

/// 抓帧失败分类：调用方据此决定重建 duplication 还是垫帧。
enum CaptureFailure {
    /// 分辨率切换/UAC 安全桌面/锁屏等导致 duplication 失效，需重建
    AccessLost,
    /// duplication 刚建立且屏幕完全静止，尚无首帧可取
    FirstFrameTimeout,
    Other(String),
}

struct DxgiCapturer {
    _device: ID3D11Device,
    context: ID3D11DeviceContext,
    duplication: IDXGIOutputDuplication,
    staging: ID3D11Texture2D,
    width: u32,
    height: u32,
    /// 上一帧像素缓存：AcquireNextFrame 超时（屏幕无变化）时复用，
    /// 并盖新墙钟，保证静止画面下水印仍按节拍推进。
    last_bgra: Option<Vec<u8>>,
}

impl DxgiCapturer {
    fn new(monitor: u32) -> Result<Self, String> {
        unsafe {
            let factory: IDXGIFactory1 = CreateDXGIFactory1()
                .map_err(|e| format!("创建 DXGI 工厂失败: {e}"))?;

            // 收集所有已接入桌面的输出（跨适配器），带上所属适配器：
            // DuplicateOutput 要求 D3D11 设备创建在输出所属的适配器上。
            let mut candidates: Vec<(IDXGIAdapter1, IDXGIOutput1, DXGI_OUTPUT_DESC)> = Vec::new();
            let mut adapter_index = 0u32;
            loop {
                let adapter = match factory.EnumAdapters1(adapter_index) {
                    Ok(a) => a,
                    Err(_) => break,
                };
                let mut output_index = 0u32;
                loop {
                    let output = match adapter.EnumOutputs(output_index) {
                        Ok(o) => o,
                        Err(_) => break,
                    };
                    output_index += 1;
                    let desc = match output.GetDesc() {
                        Ok(d) => d,
                        Err(_) => continue,
                    };
                    if !desc.AttachedToDesktop.as_bool() {
                        continue;
                    }
                    if let Ok(output1) = output.cast::<IDXGIOutput1>() {
                        candidates.push((adapter.clone(), output1, desc));
                    }
                }
                adapter_index += 1;
            }

            candidates.sort_by_key(|(_, _, d)| {
                (d.DesktopCoordinates.left, d.DesktopCoordinates.top)
            });

            // 与 capture.rs get_monitor_rect / d3d11.rs detect_monitor 保持一致：
            // monitor=1 主屏（left=0），monitor=2 副屏（left!=0），其他按序号。
            let (adapter, output1, _desc) = match monitor {
                1 => candidates
                    .iter()
                    .find(|(_, _, d)| d.DesktopCoordinates.left == 0),
                2 => candidates
                    .iter()
                    .find(|(_, _, d)| d.DesktopCoordinates.left != 0),
                _ => candidates.get((monitor as usize).saturating_sub(1)),
            }
            .ok_or_else(|| format!("DXGI 未找到显示器: monitor={monitor}"))?;

            // 在输出所属适配器上创建设备（DRIVER_TYPE 必须为 UNKNOWN）
            let mut device: Option<ID3D11Device> = None;
            let mut context: Option<ID3D11DeviceContext> = None;
            D3D11CreateDevice(
                adapter,
                D3D_DRIVER_TYPE_UNKNOWN,
                None,
                D3D11_CREATE_DEVICE_FLAG(0),
                None,
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut context),
            )
            .map_err(|e| format!("创建 D3D11 设备失败: {e}"))?;
            let device = device.ok_or("创建 D3D11 设备返回空指针")?;
            let context = context.ok_or("创建 D3D11 上下文返回空指针")?;

            let duplication = output1
                .DuplicateOutput(&device)
                .map_err(|e| format!("DuplicateOutput 失败: {e}"))?;

            let dup_desc = duplication.GetDesc();
            let rotation = dup_desc.Rotation;
            if rotation != DXGI_MODE_ROTATION_IDENTITY && rotation != DXGI_MODE_ROTATION_UNSPECIFIED
            {
                // 旋转屏需要额外的像素重排，暂不支持，交由调用方降级 GDI
                return Err(format!("显示器带旋转（rotation={}），降级 GDI", rotation.0));
            }
            let width = dup_desc.ModeDesc.Width;
            let height = dup_desc.ModeDesc.Height;
            if width == 0 || height == 0 {
                return Err("DXGI 输出尺寸为 0".to_string());
            }

            // CPU 可读的 staging 纹理，桌面帧经 CopyResource 落到这里再回读
            let staging_desc = D3D11_TEXTURE2D_DESC {
                Width: width,
                Height: height,
                MipLevels: 1,
                ArraySize: 1,
                Format: DXGI_FORMAT_B8G8R8A8_UNORM,
                SampleDesc: DXGI_SAMPLE_DESC {
                    Count: 1,
                    Quality: 0,
                },
                Usage: D3D11_USAGE_STAGING,
                BindFlags: 0,
                CPUAccessFlags: D3D11_CPU_ACCESS_READ.0 as u32,
                MiscFlags: 0,
            };
            let mut staging: Option<ID3D11Texture2D> = None;
            device
                .CreateTexture2D(&staging_desc, None, Some(&mut staging as *mut _))
                .map_err(|e| format!("创建 staging 纹理失败: {e}"))?;
            let staging = staging.ok_or("创建 staging 纹理返回空指针")?;

            Ok(Self {
                _device: device,
                context,
                duplication,
                staging,
                width,
                height,
                last_bgra: None,
            })
        }
    }

    fn capture_frame(&mut self) -> Result<CapturedFrame, CaptureFailure> {
        unsafe {
            let mut frame_info = DXGI_OUTDUPL_FRAME_INFO::default();
            let mut resource: Option<IDXGIResource> = None;
            match self
                .duplication
                .AcquireNextFrame(ACQUIRE_TIMEOUT_MS, &mut frame_info, &mut resource)
            {
                Ok(()) => {
                    // 抓帧墙钟：Acquire 返回即取（桌面内容已就绪），
                    // 与 GDI 路径同理，不混入后续纹理拷贝耗时抖动。
                    let captured_at_ms = current_timestamp_ms();
                    let texture: ID3D11Texture2D = match resource
                        .ok_or_else(|| "AcquireNextFrame 返回空资源".to_string())
                        .and_then(|r| {
                            r.cast()
                                .map_err(|e| format!("DXGI 资源转纹理失败: {e}"))
                        }) {
                        Ok(t) => t,
                        Err(e) => {
                            let _ = self.duplication.ReleaseFrame();
                            return Err(CaptureFailure::Other(e));
                        }
                    };
                    self.context.CopyResource(&self.staging, &texture);
                    let _ = self.duplication.ReleaseFrame();
                    let bgra = self.read_staging().map_err(CaptureFailure::Other)?;
                    self.last_bgra = Some(bgra.clone());
                    Ok(CapturedFrame {
                        width: self.width,
                        height: self.height,
                        bgra,
                        captured_at_ms,
                    })
                }
                Err(e) if e.code() == DXGI_ERROR_WAIT_TIMEOUT => {
                    // 屏幕无变化：桌面当前内容仍等于上一帧，复用像素、盖新墙钟
                    match &self.last_bgra {
                        Some(bgra) => Ok(CapturedFrame {
                            width: self.width,
                            height: self.height,
                            bgra: bgra.clone(),
                            captured_at_ms: current_timestamp_ms(),
                        }),
                        None => Err(CaptureFailure::FirstFrameTimeout),
                    }
                }
                Err(e) if e.code() == DXGI_ERROR_ACCESS_LOST => Err(CaptureFailure::AccessLost),
                Err(e) => Err(CaptureFailure::Other(format!(
                    "AcquireNextFrame 失败: {e}"
                ))),
            }
        }
    }

    /// 将 staging 纹理按 RowPitch 逐行紧凑拷贝为 BGRA 缓冲。
    fn read_staging(&self) -> Result<Vec<u8>, String> {
        unsafe {
            let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
            self.context
                .Map(&self.staging, 0, D3D11_MAP_READ, 0, Some(&mut mapped))
                .map_err(|e| format!("映射 staging 纹理失败: {e}"))?;

            let row_bytes = self.width as usize * 4;
            let src_pitch = mapped.RowPitch as usize;
            let mut bgra = vec![0u8; row_bytes * self.height as usize];
            for row in 0..self.height as usize {
                std::ptr::copy_nonoverlapping(
                    (mapped.pData as *const u8).add(row * src_pitch),
                    bgra.as_mut_ptr().add(row * row_bytes),
                    row_bytes,
                );
            }
            self.context.Unmap(&self.staging, 0);
            Ok(bgra)
        }
    }

    /// 用外部帧（GDI 垫帧）预置复用缓存，尺寸不符时忽略。
    fn seed_last_frame(&mut self, frame: &CapturedFrame) {
        if frame.width == self.width && frame.height == self.height {
            self.last_bgra = Some(frame.bgra.clone());
        }
    }
}

/// 抓帧源：DXGI 为主，异常场景自动降级/垫帧，供 CaptureProducer 闭包调用。
pub struct DxgiCaptureSource {
    monitor: u32,
    capturer: Mutex<Option<DxgiCapturer>>,
    /// duplication 从未成功建立（RDP/驱动不支持等）时置位，永久走 GDI
    gdi_only: AtomicBool,
    /// duplication 曾成功建立过：后续失败视为瞬态（锁屏/切分辨率），持续重试
    ever_initialized: AtomicBool,
}

impl DxgiCaptureSource {
    pub fn new(monitor: u32) -> Self {
        Self {
            monitor,
            capturer: Mutex::new(None),
            gdi_only: AtomicBool::new(false),
            ever_initialized: AtomicBool::new(false),
        }
    }

    pub fn capture(&self) -> Result<CapturedFrame, String> {
        if self.gdi_only.load(Ordering::Relaxed) {
            return capture_monitor(self.monitor);
        }
        let mut guard = self
            .capturer
            .lock()
            .map_err(|_| "dxgi capturer mutex poisoned".to_string())?;
        if guard.is_none() {
            match DxgiCapturer::new(self.monitor) {
                Ok(capturer) => {
                    self.ever_initialized.store(true, Ordering::Relaxed);
                    *guard = Some(capturer);
                }
                Err(_) => {
                    if !self.ever_initialized.load(Ordering::Relaxed) {
                        // 环境不支持 Duplication，永久降级，避免每拍白付初始化成本
                        self.gdi_only.store(true, Ordering::Relaxed);
                    }
                    // 瞬态失败（锁屏中重建失败等）：本拍用 GDI 垫帧，下拍继续重试
                    return capture_monitor(self.monitor);
                }
            }
        }
        let capturer = guard.as_mut().expect("capturer just initialized");
        match capturer.capture_frame() {
            Ok(frame) => Ok(frame),
            Err(CaptureFailure::AccessLost) => {
                // 下次调用重建 duplication；本拍丢弃，producer 会 continue
                *guard = None;
                Err("dxgi access lost, duplication will be rebuilt".to_string())
            }
            Err(CaptureFailure::FirstFrameTimeout) => {
                // 刚建立且屏幕完全静止：GDI 垫一帧并预置复用缓存
                let frame = capture_monitor(self.monitor)?;
                capturer.seed_last_frame(&frame);
                Ok(frame)
            }
            Err(CaptureFailure::Other(e)) => Err(e),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dxgi_capture_source_produces_full_bgra_frame() {
        // 有桌面会话时走 DXGI，无会话/RDP 环境自动降级 GDI，两者都应产出完整帧
        let source = DxgiCaptureSource::new(1);
        match source.capture() {
            Ok(frame) => {
                assert!(frame.width > 0 && frame.height > 0);
                assert_eq!(
                    frame.bgra.len(),
                    frame.width as usize * frame.height as usize * 4
                );
                assert!(frame.captured_at_ms > 0);
            }
            Err(_) => {
                // 无图形环境（CI 等）跳过
            }
        }
    }

    #[test]
    fn dxgi_capture_reuses_last_frame_on_static_screen() {
        let source = DxgiCaptureSource::new(1);
        let first = match source.capture() {
            Ok(frame) => frame,
            Err(_) => return, // 无图形环境跳过
        };
        // 静止画面下连续抓取应持续产出帧（超时复用路径），且墙钟不回退
        let second = source.capture().expect("second capture should succeed");
        assert_eq!(second.width, first.width);
        assert_eq!(second.height, first.height);
        assert!(second.captured_at_ms >= first.captured_at_ms);
    }
}
