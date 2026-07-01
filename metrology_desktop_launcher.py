# -*- coding: utf-8 -*-
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from tkinter import BOTH, LEFT, RIGHT, StringVar, Tk, messagebox, ttk


APP_EXE_DIR = "metrology_data_platform_v2_4"
APP_EXE_NAME = "metrology_data_platform_v2_4.exe"
CONFIG_FILE = "metrology_launcher_config.json"


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


def has_webview2_runtime() -> bool:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft" / "EdgeWebView" / "Application",
        Path.home() / "AppData" / "Local" / "Microsoft" / "EdgeWebView" / "Application",
    ]
    for folder in candidates:
        if folder.exists() and any(folder.glob("*/msedgewebview2.exe")):
            return True
    return False


def server_url(port: int, public: bool) -> str:
    host = local_ipv4() if public else "127.0.0.1"
    return f"http://{host}:{port}"


def normalize_target_url(value: str, default_port: int) -> str:
    text = (value or "").strip()
    if not text:
        text = f"127.0.0.1:{default_port}"
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        text = f"http://{text}"

    match = re.match(r"^(https?://)([^/:]+)(?::(\d+))?(.*)$", text, flags=re.IGNORECASE)
    if not match:
        return text
    scheme, host, port, path = match.groups()
    if not port:
        port = str(default_port)
    return f"{scheme}{host}:{port}{path or ''}"


def version_url(url: str) -> str:
    return url.rstrip("/") + "/version"


def is_url_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(version_url(url), timeout=1.5) as response:
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


def open_desktop_window(url: str) -> None:
    if not has_webview2_runtime():
        raise RuntimeError(
            "This PC does not appear to have Microsoft Edge WebView2 Runtime installed. "
            "Ask IT to install the WebView2 Evergreen Runtime, then start this launcher again."
        )

    try:
        import webview
    except Exception as exc:
        raise RuntimeError("pywebview runtime is missing from the launcher package.") from exc

    webview.create_window(
        "量测数据采集平台 V2.4",
        url,
        width=1280,
        height=860,
        min_size=(1024, 720),
        resizable=True,
    )
    webview.start(gui="edgechromium", debug=False)


def load_config(root: Path) -> dict:
    config_path = root / CONFIG_FILE
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(root: Path, data: dict) -> None:
    config_path = root / CONFIG_FILE
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class LauncherApp:
    def __init__(self, root_window: Tk):
        self.root_path = base_dir()
        self.root_window = root_window
        self.selected_url = ""

        config = load_config(self.root_path)
        self.mode = StringVar(value=config.get("mode", "local"))
        self.port = StringVar(value=str(config.get("port", "8023")))
        self.server = StringVar(value=config.get("server", ""))
        self.status = StringVar(value="就绪")
        self.url = StringVar(value="")

        self.root_window.title("量测数据平台桌面入口")
        self.root_window.geometry("620x405")
        self.root_window.minsize(590, 380)

        self._build_ui()
        self._refresh_url()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root_window, padding=18)
        frame.pack(fill=BOTH, expand=True)

        title = ttk.Label(frame, text="量测数据采集平台 V2.4", font=("Microsoft YaHei UI", 15, "bold"))
        title.pack(anchor="w")

        subtitle = ttk.Label(frame, text="启动后台服务，或连接已有服务器，并在独立桌面窗口中登录")
        subtitle.pack(anchor="w", pady=(4, 14))

        mode_frame = ttk.LabelFrame(frame, text="打开方式")
        mode_frame.pack(fill="x", pady=(0, 10))
        ttk.Radiobutton(mode_frame, text="本机启动服务", value="local", variable=self.mode, command=self._refresh_url).pack(anchor="w", padx=10, pady=4)
        ttk.Radiobutton(mode_frame, text="局域网服务器模式", value="lan", variable=self.mode, command=self._refresh_url).pack(anchor="w", padx=10, pady=4)
        ttk.Radiobutton(mode_frame, text="连接已有服务器", value="remote", variable=self.mode, command=self._refresh_url).pack(anchor="w", padx=10, pady=4)

        port_row = ttk.Frame(frame)
        port_row.pack(fill="x", pady=(0, 8))
        ttk.Label(port_row, text="端口").pack(side=LEFT)
        port_entry = ttk.Entry(port_row, textvariable=self.port, width=10)
        port_entry.pack(side=LEFT, padx=(8, 14))
        port_entry.bind("<KeyRelease>", lambda _event: self._refresh_url())
        ttk.Label(port_row, textvariable=self.url).pack(side=LEFT)

        server_row = ttk.Frame(frame)
        server_row.pack(fill="x", pady=(0, 10))
        ttk.Label(server_row, text="已有服务器").pack(side=LEFT)
        server_entry = ttk.Entry(server_row, textvariable=self.server, width=42)
        server_entry.pack(side=LEFT, padx=(8, 8))
        server_entry.bind("<KeyRelease>", lambda _event: self._refresh_url())
        ttk.Label(server_row, text="例：192.168.1.20 或 http://192.168.1.20:8023").pack(side=LEFT)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 12))
        ttk.Button(buttons, text="打开桌面窗口", command=self.launch_desktop).pack(side=LEFT)
        ttk.Button(buttons, text="复制地址", command=self.copy_url).pack(side=LEFT, padx=8)
        ttk.Button(buttons, text="退出", command=self.root_window.destroy).pack(side=RIGHT)

        status_box = ttk.LabelFrame(frame, text="状态")
        status_box.pack(fill=BOTH, expand=True)
        status_text = ttk.Label(status_box, textvariable=self.status, wraplength=545, justify=LEFT)
        status_text.pack(anchor="nw", fill=BOTH, expand=True, padx=10, pady=10)

    def _port_value(self) -> int:
        try:
            port = int(self.port.get().strip())
        except ValueError as exc:
            raise ValueError("端口必须是数字。") from exc
        if port < 1 or port > 65535:
            raise ValueError("端口必须在 1 到 65535 之间。")
        return port

    def _target_url(self) -> str:
        port = self._port_value()
        mode = self.mode.get()
        if mode == "remote":
            return normalize_target_url(self.server.get(), port)
        return server_url(port, public=(mode == "lan"))

    def _refresh_url(self) -> None:
        try:
            self.url.set(self._target_url())
        except ValueError:
            self.url.set("端口无效")

    def _set_status(self, text: str) -> None:
        self.status.set(text)
        self.root_window.update_idletasks()

    def _save_current_config(self) -> None:
        save_config(self.root_path, {
            "mode": self.mode.get(),
            "port": self.port.get().strip(),
            "server": self.server.get().strip(),
        })

    def launch_desktop(self) -> None:
        try:
            port = self._port_value()
            mode = self.mode.get()
            target_url = self._target_url()

            if mode in ("local", "lan"):
                app_exe = find_app_exe(self.root_path)
                if not app_exe:
                    messagebox.showerror("找不到主程序", f"请确认 {APP_EXE_DIR}\\{APP_EXE_NAME} 和本启动器在同一个包内。")
                    return

                if not is_url_ready(target_url):
                    self._set_status("正在启动后台服务...")
                    start_server(self.root_path, app_exe, port, public=(mode == "lan"))
                    for _ in range(40):
                        time.sleep(0.25)
                        if is_url_ready(target_url):
                            break

                if not is_url_ready(target_url):
                    self._set_status("后台服务启动中，但还没有响应。请稍等几秒后再试。")
                    return
            else:
                if not is_url_ready(target_url):
                    proceed = messagebox.askyesno(
                        "服务器未响应",
                        f"暂时无法确认服务器可用：\n{target_url}\n\n仍然打开桌面窗口吗？",
                    )
                    if not proceed:
                        return

            self._save_current_config()
            self.selected_url = target_url
            self.root_window.destroy()
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))
            self._set_status(f"启动失败：{exc}")

    def copy_url(self) -> None:
        self.root_window.clipboard_clear()
        self.root_window.clipboard_append(self.url.get())
        self._set_status(f"已复制地址：{self.url.get()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="run a no-GUI startup check")
    parser.add_argument("--webview-smoke", action="store_true", help="check pywebview and WebView2 availability")
    args = parser.parse_args()

    if args.smoke:
        root = base_dir()
        app_exe = find_app_exe(root)
        print(f"base_dir={root}")
        print(f"app_exe={app_exe or ''}")
        print(f"local_url={server_url(8023, False)}")
        print(f"lan_url={server_url(8023, True)}")
        print(f"webview2={'yes' if has_webview2_runtime() else 'no'}")
        return 0 if app_exe else 1

    if args.webview_smoke:
        import webview  # noqa: F401
        print(f"webview2={'yes' if has_webview2_runtime() else 'no'}")
        return 0 if has_webview2_runtime() else 1

    root_window = Tk()
    app = LauncherApp(root_window)
    root_window.mainloop()

    if app.selected_url:
        try:
            open_desktop_window(app.selected_url)
        except Exception as exc:
            error_root = Tk()
            error_root.withdraw()
            messagebox.showerror("桌面窗口启动失败", str(exc))
            error_root.destroy()
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
