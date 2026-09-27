import json
import time
from pathlib import Path

from config import UNDO_HISTORY_FILE
from alist_helper import SafeWebDAVClient, is_alist_running


def undo_moves() -> None:
    """读取 undo_history.json 并逆序原路还原所有移动过的目录"""
    if not UNDO_HISTORY_FILE.exists():
        print(f"[Undo] 未找到历史执行记录文件: {UNDO_HISTORY_FILE}")
        return

    with open(UNDO_HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)

    if not history:
        print("[Undo] 历史执行记录为空，无需撤销。")
        return

    if not is_alist_running():
        print("[Undo] 警告：AList 服务未运行，无法连接 WebDAV 执行撤销。请先启动 AList。")
        return

    client = SafeWebDAVClient()
    print("==================================================")
    print(f"【一键原路撤销恢复】")
    print(f"待逆序回滚任务数: {len(history)} 项")
    print("==================================================")

    # 使用索引追踪回滚状态，避免 remove() 值匹配的歧义
    rolled_back_indices = set()
    success_count = 0
    failed_count = 0
    skipped_count = 0

    try:
        # 逆序执行回滚
        for i in range(len(history) - 1, -1, -1):
            item = history[i]
            current_loc = item["destination"]
            original_loc = item["source"]

            print(f"[回滚] 正在将 '{current_loc}' 原路移回 '{original_loc}' ...")
            if not client.exists(current_loc):
                print(f"  [!] 目标路径已不存在（可能已被移走或删除），从记录中清除: {current_loc}")
                rolled_back_indices.add(i)
                skipped_count += 1
                continue

            try:
                ok = client.move(current_loc, original_loc, overwrite=False)
                if ok:
                    success_count += 1
                    rolled_back_indices.add(i)
                else:
                    failed_count += 1
                    print(f"  [!] 移动回滚失败: {current_loc}")
            except Exception as e:
                failed_count += 1
                print(f"  [!] 异常: {e}")
    except KeyboardInterrupt:
        print("\n" + "!" * 58)
        print("[Undo] ⚠️ 捕获到用户中断信号 (Ctrl+C)！正在安全保存回滚记录...")
        print("!" * 58)
    finally:
        # 只保留未成功回滚的条目（原子落盘）
        remaining = [item for idx, item in enumerate(history) if idx not in rolled_back_indices]
        tmp_undo = UNDO_HISTORY_FILE.with_suffix(".tmp")
        try:
            with open(tmp_undo, "w", encoding="utf-8") as f:
                json.dump(remaining, f, ensure_ascii=False, indent=2)
            tmp_undo.replace(UNDO_HISTORY_FILE)
        except Exception as e:
            print(f"[Undo] 写入回滚文件异常: {e}")

        print("==================================================")
        print(f"回滚进度报告：成功撤销: {success_count} 项, 跳过(已不存在): {skipped_count} 项, 失败: {failed_count} 项, 剩余待回滚: {len(remaining)} 项。")
        print("==================================================")


if __name__ == "__main__":
    undo_moves()
