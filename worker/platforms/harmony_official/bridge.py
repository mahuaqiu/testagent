"""HOScrcpy Java Bridge 子进程封装。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Callable

from worker.platforms.harmony_official.protocol import BridgeMessage, iter_messages


logger = logging.getLogger(__name__)


class JavaBridgeError(RuntimeError):
    """Java Bridge 启动或通信失败。"""


class JavaBridgeProcess:
    """管理官方 Java SDK 桥接进程，stdout 仅接收 HOS1 二进制协议。"""

    def __init__(
        self,
        *,
        serial: str,
        device_type: str,
        java_path: str,
        jar_path: Path,
        class_path: Path,
        hdc_path: str,
        temp_dir: Path,
        image_scale_size: int,
        frame_rate: int,
        bit_rate: int,
        on_message: Callable[[BridgeMessage], None],
        on_closed: Callable[[str], None],
    ) -> None:
        self.serial = serial
        self.device_type = device_type
        self.java_path = java_path
        self.jar_path = jar_path
        self.class_path = class_path
        self.hdc_path = hdc_path
        self.temp_dir = temp_dir
        self.image_scale_size = image_scale_size
        self.frame_rate = frame_rate
        self.bit_rate = bit_rate
        self._on_message = on_message
        self._on_closed = on_closed
        self._process: subprocess.Popen | None = None
        self._stdin_lock = threading.Lock()
        self._reader_threads: list[threading.Thread] = []

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def returncode(self) -> int | None:
        return self._process.poll() if self._process is not None else None

    def start(self) -> None:
        """启动 Java Bridge 并开始读取 stdout/stderr。"""
        if self._process is not None:
            raise JavaBridgeError("Java Bridge 已启动")
        if not self.jar_path.is_file():
            raise JavaBridgeError(f"HOScrcpy JAR 不存在: {self.jar_path}")
        if not self.class_path.is_dir():
            raise JavaBridgeError(f"StreamBridge class 目录不存在: {self.class_path}")
        if not Path(self.hdc_path).is_file():
            raise JavaBridgeError(f"HDC 不存在: {self.hdc_path}")
        self._validate_java_path()
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        classpath = os.pathsep.join((str(self.class_path), str(self.jar_path)))
        command = [
            self.java_path,
            f"-Djava.io.tmpdir={self.temp_dir}",
            "-cp",
            classpath,
            "StreamBridge",
            self.serial,
            self.device_type,
            self.hdc_path,
            "127.0.0.1",
            "8710",
            str(self.image_scale_size),
            str(self.frame_rate),
            str(self.bit_rate),
            "video",
        ]
        logger.info(
            "启动鸿蒙官方 Java Bridge: serial=%s, type=%s, fps=%s, bitrate=%s",
            self.serial,
            self.device_type,
            self.frame_rate,
            self.bit_rate,
        )
        self._process = subprocess.Popen(
            command,
            cwd=str(self.class_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        self._start_reader("stdout", self._stdout_loop, self._process.stdout)
        self._start_reader("stderr", self._stderr_loop, self._process.stderr)

    def send(self, command: bytes) -> None:
        """向 Java Bridge stdin 写入一条 UTF-8 行命令。"""
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise JavaBridgeError("Java Bridge 未运行")
        try:
            with self._stdin_lock:
                process.stdin.write(command)
                process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise JavaBridgeError(f"写入 Java Bridge 失败: {exc}") from exc

    def stop(self, timeout: float = 5.0) -> None:
        """优先优雅停止，超时后终止 Java 进程树。"""
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    self.send(b"STOP\n")
                except JavaBridgeError:
                    pass
                process.wait(timeout=max(timeout, 0.1))
        except subprocess.TimeoutExpired:
            logger.warning("Java Bridge 停止超时，终止进程树: pid=%s", process.pid)
            self._terminate_process_tree(process)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                logger.error("Java Bridge 进程树仍未退出: pid=%s", process.pid)
        finally:
            self._process = None

    def _validate_java_path(self) -> None:
        if os.path.sep in self.java_path or "/" in self.java_path:
            if not Path(self.java_path).is_file():
                raise JavaBridgeError(f"Java 运行时不存在: {self.java_path}")
        elif shutil.which(self.java_path) is None:
            raise JavaBridgeError(f"未在 PATH 找到 Java 运行时: {self.java_path}")

    def _start_reader(self, name: str, target: Callable, stream: object) -> None:
        thread = threading.Thread(
            target=target,
            args=(stream,),
            name=f"harmony-official-{name}-{self.serial}",
            daemon=True,
        )
        thread.start()
        self._reader_threads.append(thread)

    def _stdout_loop(self, stream: object) -> None:
        close_reason = "stdout EOF"
        try:
            for message in iter_messages(stream):
                self._on_message(message)
        except Exception as exc:
            close_reason = f"HOS1 读取失败: {exc}"
            logger.error("读取鸿蒙官方 Java Bridge stdout 失败: %s", exc)
        finally:
            self._on_closed(close_reason)

    def _stderr_loop(self, stream: object) -> None:
        try:
            for raw_line in iter(stream.readline, b""):
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if line:
                    logger.info("Harmony Java[%s]: %s", self.serial, line)
        except Exception as exc:
            logger.debug("读取鸿蒙官方 Java Bridge stderr 失败: %s", exc)

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    timeout=5,
                )
                if process.poll() is not None:
                    return
                if result.returncode != 0:
                    logger.warning(
                        "taskkill 未能终止 Java Bridge: pid=%s, returncode=%s, stderr=%s",
                        process.pid,
                        result.returncode,
                        result.stderr.decode(errors="replace"),
                    )
            except Exception as exc:
                logger.warning("终止 Java Bridge 进程树失败: %s", exc)
        try:
            process.kill()
            process.wait(timeout=3)
        except Exception:
            pass
