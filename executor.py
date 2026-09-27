import csv
import json
import time
from typing import List, Dict, Any, Optional
from pathlib import Path, PurePosixPath

from config import (
    PLAN_CSV_FILE,
    UNDO_HISTORY_FILE,
    EXECUTION_LOG_FILE,
    ALIST_MOUNT_PATH,
)
from alist_helper import SafeWebDAVClient


class PlanExecutor:
    """网盘整理受控单线程执行引擎（具备 Ctrl+C 优雅中断、原子断点续传、同名冲突防护、全链路日志与执行统计报告）"""

    def __init__(self, client: Optional[SafeWebDAVClient] = None):
        self.client = client

    def execute(self, plan_csv_path: Path = PLAN_CSV_FILE, dry_run: bool = False) -> None:
        """根据审核后的 CSV 计划执行移动（响应 Ctrl+C 中断，保障断点续传，并在退出时生成统计报告）"""
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
        print("【执行引擎启动】")
        print(f"模式: {'[仿真预览 (DRY RUN)]' if dry_run else '[真实执行 (LIVE RUN)]'}")
        print(f"总任务项: {len(records)} 项")
        if completed_records:
            print(f"★ 历史已完成: {len(completed_records)} 项（已自动断点跳过）")
        if skipped_records:
            print(f"用户标记跳过: {len(skipped_records)} 项")
        print(f"本次待执行: {len(pending_records)} 项")
        print(f"  - 规范两级移动: {len(move_pending)} 项")
        print(f"  - 重复副本隔离: {len(isolate_pending)} 项")
        print(f"  - 待删除软隔离: {len(delete_pending)} 项 (移动至 /_待清理隔离区/03_待删除)")
        print(f"单线程防风控延时: 1.0 ~ 2.5 秒随机延时/次")
        print(f"💡 运行提示: 随时可按 Ctrl + C 暂停，进度实时保全，下次自动断点续跑。")
        print("==================================================")

        if not pending_records:
            print("[Executor] 所有任务均已处于 COMPLETED 或 SKIP 状态，无需执行。")
            return

        undo_records: List[Dict[str, Any]] = []
        success_count = 0
        failed_count = 0
        action_stats = {"MOVE": 0, "ISOLATE": 0, "DELETE": 0}
        start_time = time.time()
        interrupted = False

        self._log_event("=" * 60)
        self._log_event(
            f"EXECUTION SESSION STARTED | Mode: {'DRY_RUN' if dry_run else 'LIVE_RUN'} | "
            f"Total: {len(records)} | History Done: {len(completed_records)} | Pending: {len(pending_records)}"
        )

        try:
            for idx, rec in enumerate(pending_records, 1):
                item_start = time.time()
                src = rec["source_path"]
                dst = rec["target_path"]
                action = rec.get("action", "MOVE")
                if rec.get("delete", "").strip().lower() == "x":
                    action = "DELETE"
                cleaned_name = rec.get("cleaned_name", "")

                # 防御性对齐挂载路径前缀：确保目标路径与源路径同属挂载目录（防止因缺少 /baiduq 前缀导致移动至 WebDAV 虚拟根目录失败）
                mount_prefix = ALIST_MOUNT_PATH.rstrip("/")
                if mount_prefix and src.startswith(mount_prefix + "/") and not dst.startswith(mount_prefix + "/"):
                    dst = f"{mount_prefix}/{dst.lstrip('/')}"

                # 冲突检测与安全后缀计算
                resolved_dst = self._resolve_target_conflict(dst, dry_run=dry_run)

                current_done = len(completed_records) + success_count
                pct = round((current_done / len(records)) * 100, 1) if records else 0.0
                print(f"[{idx}/{len(pending_records)}] [{action}] (进度: {pct}%): '{src}' -> '{resolved_dst}'")

                if dry_run:
                    success_count += 1
                    action_stats[action] = action_stats.get(action, 0) + 1
                    self._log_event(f"[{idx}/{len(pending_records)}] [DRY-RUN {action}]: '{src}' -> '{resolved_dst}'")
                    continue

                # 检查源目录是否存在（断点自愈：若源已不在原位但目标已在，视为已完成）
                if not self.client.exists(src):
                    if self.client.exists(resolved_dst):
                        print(f"  [断点自愈] 源目录已不在原位且目标已存在，标记为已完成: {resolved_dst}")
                        rec["status"] = "COMPLETED"
                        self._save_csv_progress(records, fieldnames, plan_csv_path)
                        success_count += 1
                        action_stats[action] = action_stats.get(action, 0) + 1
                        self._log_event(f"[{idx}/{len(pending_records)}] [HEALED {action}]: '{src}' -> '{resolved_dst}'")
                        continue
                    else:
                        print(f"  [!] 警告：源目录不存在，跳过: {src}")
                        rec["status"] = "FAILED"
                        self._save_csv_progress(records, fieldnames, plan_csv_path)
                        failed_count += 1
                        self._log_event(f"[{idx}/{len(pending_records)}] [SKIPPED_NOT_FOUND]: '{src}' does not exist")
                        continue

                # 真实单线程受控移动
                try:
                    ok = self.client.move(src, resolved_dst, overwrite=False)
                    item_cost = round(time.time() - item_start, 2)
                    if ok:
                        success_count += 1
                        action_stats[action] = action_stats.get(action, 0) + 1
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
                        self._log_event(f"[{idx}/{len(pending_records)}] SUCCESS [{action}] ({item_cost}s): '{src}' -> '{resolved_dst}'")
                    else:
                        failed_count += 1
                        rec["status"] = "FAILED"
                        self._save_csv_progress(records, fieldnames, plan_csv_path)
                        print(f"  [!] 移动失败: {src}")
                        self._log_event(f"[{idx}/{len(pending_records)}] FAIL [{action}] ({item_cost}s): '{src}' -> '{resolved_dst}'")
                except Exception as e:
                    failed_count += 1
                    rec["status"] = "FAILED"
                    self._save_csv_progress(records, fieldnames, plan_csv_path)
                    print(f"  [!] 发生异常: {src}, 错误: {e}")
                    self._log_event(f"[{idx}/{len(pending_records)}] ERROR [{action}]: '{src}' -> '{resolved_dst}', Error: {e}")

        except KeyboardInterrupt:
            interrupted = True
            print("\n" + "!" * 58)
            print("[Executor] ⚠️ 捕获到用户中断信号 (Ctrl+C)！正在安全保存当前执行断点...")
            print("!" * 58)
            # 强制原子落盘当前进度，确保状态百分之百安全
            self._save_csv_progress(records, fieldnames, plan_csv_path)
            self._log_event("INTERRUPTED: User pressed Ctrl+C. Execution paused safely. Current progress saved.")

        finally:
            elapsed_total = time.time() - start_time
            self._print_and_log_summary_report(
                records=records,
                pending_initial=len(pending_records),
                success_count=success_count,
                failed_count=failed_count,
                action_stats=action_stats,
                elapsed_seconds=elapsed_total,
                interrupted=interrupted,
                dry_run=dry_run,
                undo_count=len(undo_records),
                plan_csv_path=plan_csv_path,
            )

    def _save_csv_progress(self, records: List[Dict[str, Any]], fieldnames: List[str], path: Path) -> None:
        """实时将执行状态原子写回 CSV 文件，保障断点续传与进度同步（使用 .tmp 原子替换，绝不损坏原表）"""
        tmp_path = path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in records:
                    writer.writerow(r)
            tmp_path.replace(path)
        except Exception as e:
            print(f"[Executor] 同步写入 CSV 进度异常: {e}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

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
        self._log_event(f"CONFLICT RESOLVED: '{clean_target}' already exists -> Auto-renamed to '{new_target}'")
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
        tmp_undo = UNDO_HISTORY_FILE.with_suffix(".tmp")
        try:
            with open(tmp_undo, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            tmp_undo.replace(UNDO_HISTORY_FILE)
        except Exception:
            if tmp_undo.exists():
                try:
                    tmp_undo.unlink()
                except Exception:
                    pass

    def _log_event(self, text: str) -> None:
        """记录格式化日志至 execution.log"""
        EXECUTION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EXECUTION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """将秒数转为易读的人类格式 (如: 2分15秒)"""
        s = int(round(seconds))
        if s < 60:
            return f"{round(seconds, 1)} 秒"
        m, sec = divmod(s, 60)
        if m < 60:
            return f"{m} 分 {sec} 秒"
        h, m = divmod(m, 60)
        return f"{h} 小时 {m} 分 {sec} 秒"

    def _print_and_log_summary_report(
        self,
        records: List[Dict[str, Any]],
        pending_initial: int,
        success_count: int,
        failed_count: int,
        action_stats: Dict[str, int],
        elapsed_seconds: float,
        interrupted: bool,
        dry_run: bool,
        undo_count: int,
        plan_csv_path: Path,
    ) -> None:
        """打印并记录全量执行统计与断点续传报告"""
        total_records = len(records)
        completed_total = sum(1 for r in records if r.get("status", "").upper() == "COMPLETED")
        failed_total = sum(1 for r in records if r.get("status", "").upper() == "FAILED")
        skipped_total = sum(1 for r in records if r.get("status", "").upper() == "SKIP")
        pending_remaining = sum(1 for r in records if r.get("status", "").upper() == "CONFIRMED")

        pct_done = round((completed_total / total_records * 100), 1) if total_records else 0.0
        avg_speed = round(elapsed_seconds / success_count, 2) if success_count else 0.0
        dur_str = self._format_duration(elapsed_seconds)

        status_tag = "⏸️ 用户手动中断已安全暂停 (PAUSED / INTERRUPTED)" if interrupted else "✅ 当前批次执行完毕 (FINISHED)"
        if not interrupted and pending_remaining == 0 and failed_total == 0:
            status_tag = "🎉 全盘整理规划已 100% 全部完成！ (ALL COMPLETED)"

        mode_str = "仿真预览 (DRY RUN)" if dry_run else "真实执行 (LIVE RUN)"

        lines = [
            "=" * 60,
            "【整理执行统计报告 / EXECUTION SUMMARY REPORT】",
            f"执行状态: {status_tag}",
            f"执行模式: {mode_str}",
            "-" * 60,
            f"📊 全盘总览统计:",
            f"  - 全盘规划总项数: {total_records} 项",
            f"  - 累计已完成项目: {completed_total} 项 ({pct_done}%)",
            f"  - 剩余待执行项目: {pending_remaining} 项",
            f"  - 累计执行失败数: {failed_total} 项",
            f"  - 用户标记跳过数: {skipped_total} 项",
            f"",
            f"🚀 本次会话执行情况:",
            f"  - 本次成功移动: {success_count} 项",
            f"  - 本次发生失败: {failed_count} 项",
            f"  - 规范两级移动 (MOVE):    {action_stats.get('MOVE', 0)} 项",
            f"  - 重复副本隔离 (ISOLATE): {action_stats.get('ISOLATE', 0)} 项",
            f"  - 待删除软隔离 (DELETE):  {action_stats.get('DELETE', 0)} 项",
            f"",
            f"⏱️ 效率与时间消耗:",
            f"  - 本次运行总耗时: {dur_str} ({round(elapsed_seconds, 1)} 秒)",
            f"  - 平均单项处理耗时: {avg_speed} 秒/项",
            f"",
            f"🔄 断点续传说明:",
            f"  - 规划执行表已原子刷盘: {plan_csv_path}",
            f"  - 下次只需再次运行 `python run_pipeline.py execute` 即可从断点秒级继续！",
            f"",
            f"🛡️ 审计与回滚指引:",
            f"  - 本次新增回滚记录: {undo_count} 项 (历史总库: {UNDO_HISTORY_FILE})",
            f"  - 详细执行事件日志: {EXECUTION_LOG_FILE}",
            f"  - 如需紧急原路恢复已移动目录，请运行: python run_pipeline.py undo",
            "=" * 60,
        ]
        report_text = "\n".join(lines)
        print("\n" + report_text + "\n")

        # 写入日志文件
        self._log_event(f"\n{report_text}\n")
