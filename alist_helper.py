import os
import sys
import time
import json
import random
import zipfile
import subprocess
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Optional, List, Dict, Any

from webdav4.client import Client
import requests

from config import (
    BASE_DIR,
    ALIST_URL,
    WEBDAV_BASE_URL,
    WEBDAV_USERNAME,
    WEBDAV_PASSWORD,
    REQUEST_MIN_DELAY,
    REQUEST_MAX_DELAY,
    MAX_RETRIES,
    RETRY_BACKOFF,
)

ALIST_BIN_DIR = BASE_DIR / "alist_bin"
ALIST_EXE_PATH = ALIST_BIN_DIR / "alist.exe"


def is_alist_running() -> bool:
    """检查 AList 服务是否已在运行"""
    try:
        resp = requests.get(f"{ALIST_URL}/api/public/settings", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def ensure_alist_installed() -> Path:
    """确保本地已下载并解压 AList Windows 免安装版"""
    ALIST_BIN_DIR.mkdir(parents=True, exist_ok=True)
    if ALIST_EXE_PATH.exists():
        return ALIST_EXE_PATH

    print(f"[AList] 未检测到 alist.exe，开始从 GitHub 获取最新版本信息...")
    download_url = None
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/AlistGo/alist/releases/latest",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                if "windows-amd64.zip" in name and "upx" not in name:
                    download_url = asset.get("browser_download_url")
                    break
    except Exception as e:
        print(f"[AList] 获取 GitHub Release 失败 ({e})，使用稳定回退地址...")

    if not download_url:
        download_url = "https://github.com/AlistGo/alist/releases/download/v3.64.0/alist-windows-amd64.zip"

    zip_path = ALIST_BIN_DIR / "alist.zip"
    print(f"[AList] 正在下载: {download_url}")
    try:
        urllib.request.urlretrieve(download_url, zip_path)
    except Exception as e:
        # 尝试使用 ghproxy 镜像
        mirror_url = f"https://ghfast.top/{download_url}"
        print(f"[AList] 官方源下载受阻，切换镜像源: {mirror_url}")
        urllib.request.urlretrieve(mirror_url, zip_path)

    print(f"[AList] 下载完成，正在解压...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(ALIST_BIN_DIR)

    if zip_path.exists():
        zip_path.unlink()

    if not ALIST_EXE_PATH.exists():
        # 某些压缩包可能有一层子文件夹
        for p in ALIST_BIN_DIR.glob("**/alist.exe"):
            p.rename(ALIST_EXE_PATH)
            break

    print(f"[AList] 安装成功: {ALIST_EXE_PATH}")
    return ALIST_EXE_PATH


def set_admin_password(password: str = WEBDAV_PASSWORD) -> bool:
    """通过命令行将 AList 的 admin 密码设置为指定密码"""
    if not ALIST_EXE_PATH.exists():
        return False
    try:
        cmd = [str(ALIST_EXE_PATH), "admin", "set", password]
        res = subprocess.run(cmd, cwd=str(ALIST_BIN_DIR), capture_output=True, text=True, timeout=15)
        return res.returncode == 0
    except Exception as e:
        print(f"[AList] 设置初始密码失败: {e}")
        return False


def start_alist_service() -> bool:
    """启动 AList 后台进程"""
    if is_alist_running():
        print(f"[AList] AList 服务已在运行: {ALIST_URL}")
        return True

    ensure_alist_installed()
    set_admin_password(WEBDAV_PASSWORD)

    print("[AList] 正在启动 AList 服务...")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_CONSOLE

    subprocess.Popen(
        [str(ALIST_EXE_PATH), "server"],
        cwd=str(ALIST_BIN_DIR),
        creationflags=creationflags
    )

    for _ in range(15):
        time.sleep(1)
        if is_alist_running():
            print(f"[AList] AList 服务启动成功！")
            print(f"==================================================")
            print(f"管理地址: {ALIST_URL}")
            print(f"默认账号: {WEBDAV_USERNAME}")
            print(f"默认密码: {WEBDAV_PASSWORD}")
            print(f"请登录后台并在【存储】中添加【百度网盘】进行扫码绑定。")
            print(f"==================================================")
            return True

    print("[AList] 启动超时，请手动检查控制台日志。")
    return False


class SafeWebDAVClient:
    """封装带速率限制与容错重试的单线程 WebDAV 客户端"""

    def __init__(
        self,
        base_url: str = WEBDAV_BASE_URL,
        username: str = WEBDAV_USERNAME,
        password: str = WEBDAV_PASSWORD
    ):
        self.base_url = base_url.rstrip("/")
        self.client = Client(
            base_url=self.base_url,
            auth=(username, password),
            timeout=30.0
        )

    def _sleep_rate_limit(self):
        """单线程防风控延时"""
        delay = random.uniform(REQUEST_MIN_DELAY, REQUEST_MAX_DELAY)
        time.sleep(delay)

    def list_dir(self, path: str = "/") -> List[Dict[str, Any]]:
        """列出目录下的内容"""
        clean_path = path.strip()
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path

        for attempt in range(MAX_RETRIES):
            try:
                self._sleep_rate_limit()
                items = list(self.client.ls(clean_path, detail=True))
                results = []
                for item in items:
                    item_name = item.get("name", "")
                    item_full = "/" + item_name.strip("/")
                    # 过滤自身路径（webdav4 ls 返回中第一条通常是目录自身）
                    if item_full == clean_path:
                        continue
                    basename = item_name.strip("/").split("/")[-1]
                    results.append({
                        "name": basename,
                        "path": item_full,
                        "type": item.get("type", "file"),
                        "size": item.get("content_length", 0) or 0,
                        "updated_at": str(item.get("modified", ""))
                    })
                return results
            except Exception as e:
                sleep_time = RETRY_BACKOFF * (attempt + 1)
                if attempt == MAX_RETRIES - 1:
                    print(f"[WebDAV] ❌ 列目录重试耗尽失败: {clean_path}, 错误: {e}")
                    raise
                print(f"[WebDAV] ⚠️ 列目录遇到异常 ({clean_path}): {e}，第 {attempt + 1}/{MAX_RETRIES} 次重试，退避等待 {sleep_time:.1f} 秒...")
                time.sleep(sleep_time)
        return []

    def exists(self, path: str) -> bool:
        """检查路径是否存在"""
        clean_path = "/" + path.strip("/")
        try:
            return self.client.exists(clean_path)
        except Exception:
            return False

    def mkdir(self, path: str) -> bool:
        """递归创建目录"""
        clean_path = "/" + path.strip("/")
        parts = [p for p in clean_path.split("/") if p]
        curr = ""
        for p in parts:
            curr += "/" + p
            if not self.exists(curr):
                self._sleep_rate_limit()
                try:
                    self.client.mkdir(curr)
                except Exception as e:
                    # 忽略已存在的情况
                    if not self.exists(curr):
                        print(f"[WebDAV] 创建目录失败 {curr}: {e}")
                        return False
        return True

    def move(self, src_path: str, dst_path: str, overwrite: bool = False) -> bool:
        """移动目录或文件"""
        src = "/" + src_path.strip("/")
        dst = "/" + dst_path.strip("/")

        # 确保目标上级目录存在（使用 PurePosixPath 处理 WebDAV 规范路径）
        parent_dir = PurePosixPath(dst).parent.as_posix()
        if parent_dir and parent_dir != "/":
            self.mkdir(parent_dir)

        for attempt in range(MAX_RETRIES):
            try:
                self._sleep_rate_limit()
                self.client.move(src, dst, overwrite=overwrite)
                return True
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    print(f"[WebDAV] 移动失败: {src} -> {dst}, 错误: {e}")
                    return False
                time.sleep(RETRY_BACKOFF * (attempt + 1))
        return False
