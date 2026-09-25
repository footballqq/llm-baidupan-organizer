import sys
import time
from pathlib import Path

from config import (
    ALIST_MOUNT_PATH,
    PLAN_MARKDOWN_FILE,
    PLAN_CSV_FILE,
    DISK_CACHE_FILE,
    OUTPUT_DIR,
)
from alist_helper import is_alist_running, start_alist_service, SafeWebDAVClient
from scanner import NetdiskScanner
from atomic_detector import AtomicDetector
from dedup_cleaner import DedupAndCleaner
from planner import NetdiskPlanner


def run_scan_and_plan(force_rescan: bool = False) -> bool:
    """自动化运行扫描 + 智能规划一体化流程（具备断线重试与连续作业能力）"""
    print("\n" + "=" * 65)
    print("【百度网盘 LLM 智能整理系统 - 扫描与规划一体化持续作业】")
    print(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"网盘挂载点: {ALIST_MOUNT_PATH}")
    print("安全策略: 单线程 1.0~2.5s 随机延时防风控 + 多轮断线自动重试")
    print("=" * 65 + "\n")

    # 1. 确保 AList 处于正常运行状态
    if not is_alist_running():
        print("[Pipeline] 检测到 AList 服务未运行，正在拉起...")
        if not start_alist_service():
            print("[Pipeline] ❌ AList 服务拉起失败，请检查端口 5244 是否被占用。")
            return False
    else:
        print("[Pipeline] ✓ AList 服务处于就绪状态。")

    # 2. 检查 WebDAV 连通性
    client = SafeWebDAVClient()
    mount_target = ALIST_MOUNT_PATH.strip("/")
    mount_root = "/" + mount_target if mount_target else "/"

    print(f"[Pipeline] 正在测试网盘连通性 ({mount_root})...")
    connected = False
    for attempt in range(5):
        try:
            test_items = client.list_dir(mount_root)
            print(f"[Pipeline] ✓ 网盘连接成功！根目录下发现 {len(test_items)} 个直接项。")
            connected = True
            break
        except Exception as e:
            wait_s = 5 * (attempt + 1)
            print(f"[Pipeline] ⚠️ 网盘连接测试未就绪 ({e})，第 {attempt + 1}/5 次重试，等待 {wait_s} 秒...")
            time.sleep(wait_s)

    if not connected:
        print(f"[Pipeline] ❌ 无法连接至网盘路径 '{mount_root}'，请检查 AList 中【存储】的挂载路径与刷新令牌。")
        return False

    # 3. 执行单线程安全递归扫描（断点自动续扫）
    print("\n" + "-" * 50)
    print("【阶段一：全盘目录树递归扫描】")
    print("-" * 50)
    scanner = NetdiskScanner(client)
    try:
        tree = scanner.scan_tree(root_path=mount_root, resume=not force_rescan)
    except Exception as e:
        print(f"[Pipeline] ❌ 扫描过程异常终止: {e}")
        return False

    if scanner.failed_paths:
        print(f"[Pipeline] ⚠️ 提示：扫描完成，但有 {len(scanner.failed_paths)} 个目录因网络不可达跳过，已记录到日志。")

    # 4. 执行原子聚类、查重去广告与目标结构规划
    print("\n" + "-" * 50)
    print("【阶段二：大模型原子判定、查重净化与规划生成】")
    print("-" * 50)

    try:
        # 原子目录与容器拆解
        detector = AtomicDetector()
        atomic_units = detector.extract_atomic_units(tree)

        # 查重与名称去广告规范化
        cleaner = DedupAndCleaner()
        unique_units, duplicates = cleaner.process(atomic_units)

        # 归类映射规划
        planner = NetdiskPlanner()
        plan_records = planner.generate_plan(unique_units, duplicates)
    except Exception as e:
        print(f"[Pipeline] ❌ 大模型规划阶段异常: {e}")
        return False

    # 5. 写入持续作业总结报告
    summary_path = OUTPUT_DIR / "scan_and_plan_summary.txt"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("百度网盘 LLM 智能整理系统 - 扫描与规划完成报告\n")
        f.write(f"完成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"全盘扫描总目录数: {scanner.stats['total_folders']}\n")
        f.write(f"全盘扫描总文件数: {scanner.stats['total_files']}\n")
        f.write(f"全盘总大小: {round(scanner.stats['total_size_bytes'] / (1024**3), 2)} GB\n")
        f.write(f"提取独立原子资源单元: {len(unique_units)} 个\n")
        f.write(f"检出确凿重复副本: {len(duplicates)} 个\n")
        f.write(f"生成整理规划项: {len(plan_records)} 项\n")
        f.write(f"\n审核报告位置:\n- Markdown 汇总报告: {PLAN_MARKDOWN_FILE}\n- CSV 详细明细表: {PLAN_CSV_FILE}\n")

    print("\n" + "=" * 65)
    print("🎉【扫描与智能规划流程已圆满完成！】")
    print(f"1. 规划汇总报告: {PLAN_MARKDOWN_FILE}")
    print(f"2. 可编辑明细表: {PLAN_CSV_FILE}")
    print(f"3. 作业总结: {summary_path}")
    print("请你在睡眠醒来后查看 Markdown 报告或用 Excel 查看 CSV 明细表。")
    print("确认无误后，只需运行 `python run_pipeline.py execute` 即可开始安全受控移动！")
    print("=" * 65 + "\n")
    return True


if __name__ == "__main__":
    force = "--force" in sys.argv
    run_scan_and_plan(force_rescan=force)
