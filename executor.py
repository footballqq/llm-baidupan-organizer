import csv
import json
import time
from typing import List, Dict, Any, Optional
from pathlib import Path, PurePosixPath

from config import (
    PLAN_CSV_FILE,
    UNDO_HISTORY_FILE,
    EXECUTION_LOG_FILE,
)
from alist_helper import SafeWebDAVClient


class PlanExecutor:
    """网盘整理受控单线程执行引擎（具备断点续执、同名冲突防护与全量回滚日志记录）"""

    def __init__(self, client: Optional[SafeWebDAVClient] = None):
        self.client = client

    def execute(self, plan_csv_path: Path = PLAN_CSV_FILE, dry_run: bool = False) -> None:
        """根据审核后的 CSV 计划执行移动（支持断点续执）"""
        if not plan_csv_path.exists():
            print(f"[Executor] 错误：未找到规划表文件 {plan_csv_path}，请先执行 plan 生成规划。")
            return

        with open(plan_csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            records = list(reader)

        completed_records = [r for r in records if r.get("status", "").upper() == "COMPLETED"]
        skipped_records = [r for r in records if r.get("status", "").upper() == "SKIP"]
        pending_records = [r for r in records if r.get("status", "").upper() == "CONFIRMED"]

        move_pending = [r for r in pending_records if r.get("action") == "MOVE" and r.get("delete", "").strip().lower() != "x"]
        isolate_pending = [r for r in pending_records if r.get("action") == "ISOLATE" and r.get("delete", "").strip().lower() != "x"]
        delete_pending = [r for r in pending_records if r.get("action") == "DELETE" or r.get("delete", "").strip().lower() == "x"]

        print("==================================================")
        print(f"【执行引擎启动】")
        print(f"模式: {'[仿真预览 (DRY RUN)]' if dry_run else '[真实执行 (LIVE RUN)]'}")
        print(f"总任务项: {len(records)} 项")
        if completed_records:
            print(f"★ 检测到历史已完成任务: {len(completed_records)} 项（已自动断点续跳过）")
        if skipped_records:
            print(f"用户标记跳过: {len(skipped_records)} 项")
        print(f"本次待执行: {len(pending_records)} 项")
        print(f"  - 规范两级移动: {len(move_pending)} 项")
        print(f"  - 重复副本隔离: {len(isolate_pending)} 项")
        print(f"  - 待删除软隔离: {len(delete_pending)} 项 (移动至 /_待清理隔离区/03_待删除)")
        print(f"单线程防风控延时: 1.0 ~ 2.5 秒随机延时/次")
        print("==================================================")

        if not pending_records:
            print("[Executor] 所有任务均已处于 COMPLETED 或 SKIP 状态，无需执行。")
            return

        undo_records: List[Dict[str, Any]] = []
        success_count = 0
        failed_count = 0

        for idx, rec in enumerate(pending_records, 1):
            src = rec["source_path"]
            dst = rec["target_path"]
            action = rec.get("action", "MOVE")
            if rec.get("delete", "").strip().lower() == "x":
                action = "DELETE"
            cleaned_name = rec.get("cleaned_name", "")

            # 冲突检测与安全后缀计算
            resolved_dst = self._resolve_target_conflict(dst, dry_run=dry_run)

            print(f"[{idx}/{len(pending_records)}] [{action}] : '{src}' -> '{resolved_dst}'")

            if dry_run:
                success_count += 1
                continue

            # 检查源目录是否存在（断点自愈：若源已不存在但目标已在，视为已完成）
            if not self.client.exists(src):
                if self.client.exists(resolved_dst):
                    print(f"  [断点自愈] 源目录已不在原位且目标已存在，标记为已完成: {resolved_dst}")
                    rec["status"] = "COMPLETED"
                    self._save_csv_progress(records, fieldnames, plan_csv_path)
                    success_count += 1
                    continue
                else:
                    print(f"  [!] 警告：源目录不存在，跳过: {src}")
                    rec["status"] = "FAILED"
                    self._save_csv_progress(records, fieldnames, plan_csv_path)
                    failed_count += 1
                    continue

            # 真实单线程执行
            try:
                ok = self.client.move(src, resolved_dst, overwrite=False)
                if ok:
                    success_count += 1
                    rec["status"] = "COMPLETED"
                    self._save_csv_progress(records, fieldnames, plan_csv_path)

                    undo_entry = {
                        "id": rec.get("id"),
                        "action": action,
                        "source": src,
                        "destination": resolved_dst,
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
                    undo_records.append(undo_entry)
                    self._append_undo_record(undo_entry)
                    self._log_event(f"SUCCESS [{action}]: {src} -> {resolved_dst}")
                else:
                    failed_count += 1
                    rec["status"] = "FAILED"
                    self._save_csv_progress(records, fieldnames, plan_csv_path)
                    print(f"  [!] 移动失败: {src}")
                    self._log_event(f"FAIL [{action}]: {src} -> {resolved_dst}")
            except Exception as e:
                failed_count += 1
                rec["status"] = "FAILED"
                self._save_csv_progress(records, fieldnames, plan_csv_path)
                print(f"  [!] 发生异常: {src}, 错误: {e}")
                self._log_event(f"ERROR [{action}]: {src} -> {resolved_dst}, Error: {e}")

        print("==================================================")
        print(f"执行完毕！本次成功: {success_count}, 失败: {failed_count}, 累计已完成: {len(completed_records) + success_count}")
        if not dry_run and undo_records:
            print(f"撤销记录已实时归档至: {UNDO_HISTORY_FILE}")
            print(f"如需还原，请运行: python run_pipeline.py undo")
        print("==================================================")

    def _save_csv_progress(self, records: List[Dict[str, Any]], fieldnames: List[str], path: Path) -> None:
        """实时将执行状态写回 CSV 文件，保障断点续传与进度同步"""
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in records:
                    writer.writerow(r)
        except Exception as e:
            print(f"[Executor] 同步写入 CSV 进度异常: {e}")

    def _resolve_target_conflict(self, target_path: str, dry_run: bool = False) -> str:
        """检查目标路径是否已存在同名目录，若存在则追加 _副本 后缀"""
        clean_target = "/" + target_path.strip("/")
        if dry_run or not self.client:
            return clean_target

        if not self.client.exists(clean_target):
            return clean_target

        # 存在同名冲突，追加 _副本N 后缀
        parent = PurePosixPath(clean_target).parent.as_posix()
        name = PurePosixPath(clean_target).name
        suffix_idx = 1
        new_target = f"{parent}/{name}_副本{suffix_idx}"

        while self.client.exists(new_target):
            suffix_idx += 1
            new_target = f"{parent}/{name}_副本{suffix_idx}"

        print(f"  [冲突防护] 目标 '{clean_target}' 已存在，自动重命名为 '{new_target}' 严防覆盖！")
        return new_target

    def _append_undo_record(self, entry: Dict[str, Any]) -> None:
        """追加记录至 undo_history.json"""
        UNDO_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        history = []
        if UNDO_HISTORY_FILE.exists():
            try:
                with open(UNDO_HISTORY_FILE, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []
        history.append(entry)
        with open(UNDO_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def _log_event(self, text: str) -> None:
        """记录日志至 execution.log"""
        EXECUTION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EXECUTION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
