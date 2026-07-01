# -*- coding: utf-8 -*-
import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, StringVar, Tk, messagebox, ttk


APP_EXE_DIR = "metrology_data_platform_v2_4"
APP_EXE_NAME = "metrology_data_platform_v2_4.exe"


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_app_exe(root: Path) -> Path | None:
    candidates = [
        root / APP_EXE_DIR / APP_EXE_NAME,
        root / "dist_exe" / APP_EXE_DIR / APP_EXE_NAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def local_ipv4() -> str:
    try:
        addresses = socket.gethostbyname_ex(socket.gethostname())[2]
        for address in addresses:
            if not address.startswith("127.") and not address.startswith("169.254."):
                return address
    except OSError:
        pass
    return "127.0.0.1"


def resolve_tesseract(root: Path) -> str:
    configured = os.environ.get("MDCP_TESSERACT_CMD", "").strip()
    candidates = [
        configured,
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        str(Path.home() / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
        str(root / "Tesseract-OCR" / "tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
    return ""


def server_url(port: int, public: bool) -> str:
    host = local_ipv4() if public else "127.0.0.1"
    return f"http://{host}:{port}"


def version_url(port: int, public: bool) -> str:
    return f"{server_url(port, public)}/version"


def is_server_ready(port: int, public: bool) -> bool:
    try:
        with urllib.request.urlopen(version_url(port, public), timeout=1.5) as response:
            text = response.read(512).decode("utf-8", errors="ignore")
            return response.status == 200 and "V2.4" in text
    except Exception:
        return False


def start_server(root: Path, app_exe: Path, port: int, public: bool) -> None:
    host = "0.0.0.0" if public else "127.0.0.1"
    env = os.environ.copy()
    env["MDCP_HOST"] = host
    env["MDCP_PORT"] = str(port)

    tesseract = resolve_tesseract(root)
    if tesseract:
        env["MDCP_TESSERACT_CMD"] = tesseract

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        [str(app_exe)],
        cwd=str(root),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


class LauncherApp:
    def __init__(self, root_window: Tk):
        self.root_path = base_dir()
        self.root_window = root_window
        self.root_window.title("量测数据平台登录入口")
        self.root_window.geometry("520x330")
        self.root_window.minsize(500, 310)

        self.mode = StringVar(value="local")
        self.port = StringVar(value="8023")
        self.status = StringVar(value="就绪")
        self.url = StringVar(value=server_url(8023, False))

        self._build_ui()
        self._refresh_url()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root_window, padding=18)
        frame.pack(fill=BOTH, expand=True)

        title = ttk.Label(frame, text="量测数据采集平台 V2.4", font=("Microsoft YaHei UI", 15, "bold"))
        title.pack(anchor="w")

        subtitle = ttk.Label(frame, text="双击入口用于启动后台服务并打开登录页")
        subtitle.pack(anchor="w", pady=(4, 14))

        mode_frame = ttk.LabelFrame(frame, text="启动模式")
        mode_frame.pack(fill="x", pady=(0, 10))
        ttk.Radiobutton(mode_frame, text="仅本机使用", value="local", variable=self.mode, command=self._refresh_url).pack(anchor="w", padx=10, pady=5)
        ttk.Radiobutton(mode_frame, text="局域网服务器模式", value="lan", variable=self.mode, command=self._refresh_url).pack(anchor="w", padx=10, pady=5)

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text="端口").pack(side=LEFT)
        port_entry = ttk.Entry(row, textvariable=self.port, width=10)
        port_entry.pack(side=LEFT, padx=(8, 12))
        port_entry.bind("<KeyRelease>", lambda _event: self._refresh_url())
        ttk.Label(row, textvariable=self.url).pack(side=LEFT)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 12))
        ttk.Button(buttons, text="启动并打开登录页", command=self.start_and_open).pack(side=LEFT)
        ttk.Button(buttons, text="只打开登录页", command=self.open_login).pack(side=LEFT, padx=8)
        ttk.Button(buttons, text="复制地址", command=self.copy_url).pack(side=LEFT)
        ttk.Button(buttons, text="退出", command=self.root_window.destroy).pack(side=RIGHT)

        status_box = ttk.LabelFrame(frame, text="状态")
        status_box.pack(fill=BOTH, expand=True)
        self.status_text = ttk.Label(status_box, textvariable=self.status, wraplength=460, justify=LEFT)
        self.status_text.pack(anchor="nw", fill=BOTH, expand=True, padx=10, pady=10)

    def _port_value(self) -> int:
        try:
            port = int(self.port.get().strip())
        except ValueError as exc:
            raise ValueError("端口必须是数字。") from exc
        if port < 1 or port > 65535:
            raise ValueError("端口必须在 1 到 65535 之间。")
        return port

    def _public_mode(self) -> bool:
        return self.mode.get() == "lan"

    def _refresh_url(self) -> None:
        try:
            self.url.set(server_url(self._port_value(), self._public_mode()))
        except ValueError:
            self.url.set("端口无效")

    def _set_status(self, text: str) -> None:
        self.status.set(text)
        self.root_window.update_idletasks()

    def start_and_open(self) -> None:
        try:
            port = self._port_value()
            public = self._public_mode()
            app_exe = find_app_exe(self.root_path)
            if not app_exe:
                messagebox.showerror("找不到主程序", f"请确认 {APP_EXE_DIR}\\{APP_EXE_NAME} 和本启动器放在同一个包内。")
                return

            target_url = server_url(port, public)
            if not is_server_ready(port, public):
                self._set_status("正在启动后台服务...")
                start_server(self.root_path, app_exe, port, public)
                for _ in range(40):
                    time.sleep(0.25)
                    if is_server_ready(port, public):
                        break

            if not is_server_ready(port, public):
                self._set_status("后台服务启动中，但暂时还没有响应。请稍等几秒后再点“只打开登录页”。")
                return

            webbrowser.open(target_url)
            self._set_status(f"已打开登录页：{target_url}")
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))
            self._set_status(f"启动失败：{exc}")

    def open_login(self) -> None:
        try:
            port = self._port_value()
            target_url = server_url(port, self._public_mode())
            webbrowser.open(target_url)
            self._set_status(f"已打开：{target_url}")
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def copy_url(self) -> None:
        self.root_window.clipboard_clear()
        self.root_window.clipboard_append(self.url.get())
        self._set_status(f"已复制地址：{self.url.get()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="run a no-GUI startup check")
    args = parser.parse_args()
    if args.smoke:
        root = base_dir()
        app_exe = find_app_exe(root)
        print(f"base_dir={root}")
        print(f"app_exe={app_exe or ''}")
        print(f"local_url={server_url(8023, False)}")
        print(f"lan_url={server_url(8023, True)}")
        return 0 if app_exe else 1

    root_window = Tk()
    LauncherApp(root_window)
    root_window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
