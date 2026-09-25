import sys
import argparse
from pathlib import Path

from config import (
    DISK_CACHE_FILE,
    PLAN_MARKDOWN_FILE,
    PLAN_CSV_FILE,
    ALIST_URL,
    ALIST_MOUNT_PATH,
)
from alist_helper import (
    start_alist_service,
    is_alist_running,
    SafeWebDAVClient,
)
from scanner import NetdiskScanner
from atomic_detector import AtomicDetector
from dedup_cleaner import DedupAndCleaner
from planner import NetdiskPlanner
from executor import PlanExecutor
from undo_organize import undo_moves


def cmd_init():
    """初始化并启动 AList 环境"""
    print("[Pipeline] 检查并准备 AList 服务环境...")
    ok = start_alist_service()
    if ok:
        print("\n【AList 绑定百度网盘指引】")
        print("1. 浏览器已支持访问后台: " + ALIST_URL)
        print("2. 登录账号: admin  密码: admin123")
        print("3. 点击【管理】 -> 【存储】 -> 【添加】:")
        print("   - 驱动: 选择【百度网盘】 (Baidu.Photo / BaiduNetdisk)")
        print("   - 挂载路径: 建议填 `/`（或自定义名称如 `/baidu`）")
        print("   - 刷新令牌 (Refresh Token): 点击获取/扫码登录即可绑定")
        print("4. 绑定成功后，在 AList 首页能够浏览网盘文件，即可开始执行 `python run_pipeline.py scan`\n")


def cmd_scan(root_path: str = "/", force: bool = False):
    """单线程递归扫描网盘目录树并保存本地快照"""
    if not is_alist_running():
        print(f"[Pipeline] 警告：AList 服务未运行，正在尝试自动拉起...")
        if not start_alist_service():
            print("[Pipeline] AList 启动失败，请检查端口占用。")
            return

    if not force and DISK_CACHE_FILE.exists():
        print(f"[Pipeline] 检测到已有扫描快照: {DISK_CACHE_FILE}")
        choice = input("是否直接使用已有快照？(Y/n，输入 n 重新扫描): ").strip().lower()
        if choice != "n":
            print("[Pipeline] 使用已有快照。如需重新扫描，请使用参数 --force。")
            return

    client = SafeWebDAVClient()
    scanner = NetdiskScanner(client)
    mount_root = ALIST_MOUNT_PATH.rstrip("/") + "/" + root_path.strip("/")
    scanner.scan_tree(root_path=mount_root)


def cmd_plan():
    """执行原子判定、指纹查重、名称净化与 LLM 分类规划，输出审核文档"""
    tree = NetdiskScanner.load_cache()
    if not tree:
        print("[Pipeline] 错误：未发现扫描快照，请先运行 `python run_pipeline.py scan`。")
        return

    # 1. 原子单元判定
    detector = AtomicDetector()
    atomic_units = detector.extract_atomic_units(tree)

    # 2. 查重与名称清洗
    cleaner = DedupAndCleaner()
    unique_units, duplicates = cleaner.process(atomic_units)

    # 3. 规划映射生成
    planner = NetdiskPlanner()
    plan_records = planner.generate_plan(unique_units, duplicates)

    print("\n" + "=" * 60)
    print("【规划方案生成完成！请核对审核】")
    print(f"1. 方案摘要与统计报告: {PLAN_MARKDOWN_FILE}")
    print(f"2. 详细执行表格 (Excel/CSV): {PLAN_CSV_FILE}")
    print("3. 你可以直接在 Excel 中打开 CSV 文件，核对目标路径，或将不希望移动的行 status 改为 SKIP。")
    print("4. 核对无误后，执行 `python run_pipeline.py execute --dry-run` 仿真预览或 `python run_pipeline.py execute` 真实移动。")
    print("=" * 60 + "\n")


def cmd_execute(dry_run: bool = False):
    """根据审核后的 CSV 计划执行移动"""
    if not dry_run and not is_alist_running():
        print("[Pipeline] 错误：AList 未运行，无法执行真实网盘移动。请先运行 `python run_pipeline.py init` 启动 AList。")
        return

    client = SafeWebDAVClient() if is_alist_running() else None
    executor = PlanExecutor(client)
    executor.execute(plan_csv_path=PLAN_CSV_FILE, dry_run=dry_run)


def cmd_refresh_plan():
    """从现有 organize_plan.csv 刷新切片与方案报告（同步用户打 x 的待删除与自定义修改）"""
    planner = NetdiskPlanner()
    planner.refresh_plan_exports(PLAN_CSV_FILE)


def cmd_undo():
    """原路撤销回滚"""
    undo_moves()


def main():
    parser = argparse.ArgumentParser(
        description="LLM 百度网盘智能整理与归类系统 (LLM-BaiduPan-Organizer)",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # init
    subparsers.add_parser("init", help="初始化并拉起 AList 绿色版环境")

    # scan
    scan_parser = subparsers.add_parser("scan", help="递归单线程扫描网盘结构")
    scan_parser.add_argument("--root", default="/", help="指定扫描起始路径（默认根目录 /）")
    scan_parser.add_argument("--force", action="store_true", help="强制忽略本地快照重新全盘扫描")

    # plan
    subparsers.add_parser("plan", help="运行 LLM 原子判定、查重、净化与方案生成")

    # scan-plan
    sp_parser = subparsers.add_parser("scan-plan", help="连续作业：自动完成扫描并紧接着执行智能规划")
    sp_parser.add_argument("--force", action="store_true", help="强制忽略本地快照重新全盘扫描")

    # execute
    exec_parser = subparsers.add_parser("execute", help="读取审核后的 plan 执行单线程移动")
    exec_parser.add_argument("--dry-run", action="store_true", help="仿真预览模式（不真实移动任何文件）")

    # refresh-plan
    subparsers.add_parser("refresh-plan", help="根据 organize_plan.csv 中的人工标注（如 delete=x、目标路径）同步刷新切片与报告")

    # undo
    subparsers.add_parser("undo", help="依据 undo_history.json 一键原路撤销移动")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "scan":
        cmd_scan(root_path=args.root, force=args.force)
    elif args.command == "plan":
        cmd_plan()
    elif args.command == "scan-plan":
        from scan_and_plan import run_scan_and_plan
        run_scan_and_plan(force_rescan=args.force)
    elif args.command == "refresh-plan":
        cmd_refresh_plan()
    elif args.command == "execute":
        cmd_execute(dry_run=args.dry_run)
    elif args.command == "undo":
        cmd_undo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
