import json
import time
from typing import Dict, Any, List, Optional, Set
from pathlib import Path

from config import DISK_CACHE_FILE, SCAN_CHECKPOINT_FILE
from alist_helper import SafeWebDAVClient


class NetdiskScanner:
    """递归遍历网盘目录树并持久化至本地缓存的单线程扫描器（支持断点续扫与安全随机延时）"""

    def __init__(self, client: SafeWebDAVClient):
        self.client = client
        self.stats = {
            "total_folders": 0,
            "total_files": 0,
            "total_size_bytes": 0,
            "extensions": {}
        }
        # 断点缓存：已爬取过的目录及其返回的子项列表 { path: [items] }
        self.visited_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._new_visited_counter = 0
        self.failed_paths: List[Dict[str, str]] = []

    def scan_tree(self, root_path: str = "/", max_depth: int = 10, resume: bool = True) -> Dict[str, Any]:
        """从指定根目录开始递归单线程扫描整棵树（具备断点续扫能力）"""
        root_path = "/" + root_path.strip("/")
        print(f"[Scanner] 开始从 '{root_path}' 递归扫描网盘结构（单线程随机延时防风控）...")
        start_time = time.time()
        self.stats = {
            "total_folders": 0,
            "total_files": 0,
            "total_size_bytes": 0,
            "extensions": {}
        }
        self._new_visited_counter = 0

        # 尝试加载历史断点快照
        if resume and SCAN_CHECKPOINT_FILE.exists():
            try:
                with open(SCAN_CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                    ckpt = json.load(f)
                    self.visited_cache = ckpt.get("visited_cache", {})
                print(f"[Scanner] ★ 成功加载上次未完成的断点快照！已缓存 {len(self.visited_cache)} 个目录，跳过重复请求。")
            except Exception as e:
                print(f"[Scanner] 断点文件加载异常 ({e})，重新开始扫描。")
                self.visited_cache = {}

        try:
            tree = self._scan_node(root_path, depth=0, max_depth=max_depth)
        except KeyboardInterrupt:
            print("\n[Scanner] 捕获用户中断！正在保存当前断点快照...")
            self._save_checkpoint()
            print(f"[Scanner] 断点已保存至 {SCAN_CHECKPOINT_FILE}。下次运行将直接从断点继续。")
            raise

        elapsed = round(time.time() - start_time, 2)
        print(f"[Scanner] 扫描全部完成！耗时: {elapsed} 秒")
        print(f"[Scanner] 统计: 目录数={self.stats['total_folders']}, 文件数={self.stats['total_files']}, 总大小={round(self.stats['total_size_bytes'] / (1024**3), 2)} GB")

        # 保存完整快照并清除断点临时文件
        self.save_cache(tree)
        if SCAN_CHECKPOINT_FILE.exists():
            SCAN_CHECKPOINT_FILE.unlink(missing_ok=True)

        return tree

    def _scan_node(self, path: str, depth: int, max_depth: int) -> Dict[str, Any]:
        """递归扫描单个目录节点"""
        self.stats["total_folders"] += 1
        node_name = path.rstrip("/").split("/")[-1] or "root"
        node: Dict[str, Any] = {
            "name": node_name,
            "path": path,
            "type": "directory",
            "depth": depth,
            "sub_dirs": [],
            "sub_files": [],
            "total_files": 0,
            "total_size": 0,
            "ext_summary": {}
        }

        if depth >= max_depth:
            print(f"[Scanner] 达到最大深度 {max_depth}，跳过下探: {path}")
            return node

        # 检查是否命中已有的断点缓存
        if path in self.visited_cache:
            items = self.visited_cache[path]
        else:
            items = None
            for outer_attempt in range(3):
                try:
                    items = self.client.list_dir(path)
                    self.visited_cache[path] = items
                    self._new_visited_counter += 1
                    # 每扫描 5 个新目录自动写盘保存一次断点，避免意外中断丢失
                    if self._new_visited_counter % 5 == 0:
                        self._save_checkpoint()
                    break
                except Exception as e:
                    if outer_attempt < 2:
                        wait_s = 15 * (outer_attempt + 1)
                        print(f"[Scanner] ⚠️ 网络或网盘服务疑似中断，休眠等待 {wait_s} 秒后自动尝试恢复 ({path})...")
                        time.sleep(wait_s)
                    else:
                        print(f"[Scanner] ❌ 该目录跳过并记入检查点: {path}, 错误: {e}")
                        self.failed_paths.append({"path": path, "error": str(e)})
                        self._save_checkpoint()
                        return node

        sub_dirs_to_recurse = []
        for item in items:
            itype = item.get("type", "file")
            ipath = item.get("path", "")
            isize = item.get("size", 0) or 0
            iname = item.get("name", "")

            if itype == "directory":
                sub_dirs_to_recurse.append(ipath)
            else:
                self.stats["total_files"] += 1
                self.stats["total_size_bytes"] += isize
                ext = Path(iname).suffix.lower() or "unknown"
                self.stats["extensions"][ext] = self.stats["extensions"].get(ext, 0) + 1

                node["sub_files"].append({
                    "name": iname,
                    "path": ipath,
                    "size": isize,
                    "ext": ext
                })
                node["total_files"] += 1
                node["total_size"] += isize
                node["ext_summary"][ext] = node["ext_summary"].get(ext, 0) + 1

        # 递归扫描所有子目录
        for sub_dir_path in sub_dirs_to_recurse:
            child_node = self._scan_node(sub_dir_path, depth=depth + 1, max_depth=max_depth)
            node["sub_dirs"].append(child_node)
            node["total_files"] += child_node.get("total_files", 0)
            node["total_size"] += child_node.get("total_size", 0)
            for ext, count in child_node.get("ext_summary", {}).items():
                node["ext_summary"][ext] = node["ext_summary"].get(ext, 0) + count

        return node

    def _save_checkpoint(self) -> None:
        """保存当前未完成的扫描断点（原子写入，防崩溃截断）"""
        SCAN_CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "visited_count": len(self.visited_cache),
            "visited_cache": self.visited_cache
        }
        tmp_file = SCAN_CHECKPOINT_FILE.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        tmp_file.replace(SCAN_CHECKPOINT_FILE)

    def save_cache(self, tree: Dict[str, Any], filepath: Path = DISK_CACHE_FILE) -> None:
        """保存扫描树快照至本地缓存"""
        filepath.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cached_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stats": self.stats,
            "tree": tree
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[Scanner] 目录树完整快照已成功持久化至缓存: {filepath}")

    @staticmethod
    def load_cache(filepath: Path = DISK_CACHE_FILE) -> Optional[Dict[str, Any]]:
        """从本地缓存加载树状快照"""
        if not filepath.exists():
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("tree")
        except Exception as e:
            print(f"[Scanner] 加载缓存失败: {e}")
            return None
