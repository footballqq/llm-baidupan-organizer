import json
import shutil
from pathlib import Path

from config import (
    BASE_DIR,
    DISK_CACHE_FILE,
    PLAN_MARKDOWN_FILE,
    PLAN_CSV_FILE
)
from atomic_detector import AtomicDetector
from dedup_cleaner import DedupAndCleaner
from planner import NetdiskPlanner
from executor import PlanExecutor


def run_mock_test():
    print(">>> 开始端到端模拟测试数据流...")

    # 构建模拟测试树结构
    mock_tree = {
        "name": "root",
        "path": "/",
        "type": "directory",
        "total_files": 120,
        "total_size": 15000000000,
        "sub_files": [],
        "sub_dirs": [
            {
                "name": "学习",
                "path": "/学习",
                "type": "directory",
                "total_files": 40,
                "total_size": 2000000000,
                "sub_files": [],
                "sub_dirs": [
                    {
                        "name": "AMC8",
                        "path": "/学习/AMC8",
                        "type": "directory",
                        "total_files": 20,
                        "total_size": 500000000,
                        "sub_files": [],
                        "sub_dirs": [
                            {"name": "2001", "path": "/学习/AMC8/2001", "type": "directory", "total_files": 5, "total_size": 100000000, "sub_dirs": [], "sub_files": [{"name": "2001真题.pdf", "size": 20000000}]},
                            {"name": "2002", "path": "/学习/AMC8/2002", "type": "directory", "total_files": 5, "total_size": 100000000, "sub_dirs": [], "sub_files": [{"name": "2002真题.pdf", "size": 20000000}]},
                            {"name": "2003", "path": "/学习/AMC8/2003", "type": "directory", "total_files": 5, "total_size": 100000000, "sub_dirs": [], "sub_files": [{"name": "2003真题.pdf", "size": 20000000}]}
                        ]
                    },
                    {
                        "name": "一年级数学口算大卡",
                        "path": "/学习/一年级数学口算大卡",
                        "type": "directory",
                        "total_files": 10,
                        "total_size": 50000000,
                        "sub_dirs": [],
                        "sub_files": [{"name": "10以内加减法.pdf", "size": 5000000}]
                    }
                ]
            },
            {
                "name": "乐高EV3机器人搭建高清图纸",
                "path": "/乐高EV3机器人搭建高清图纸",
                "type": "directory",
                "total_files": 8,
                "total_size": 300000000,
                "sub_dirs": [],
                "sub_files": [{"name": "机械臂搭建.pdf", "size": 30000000}]
            },
            {
                "name": "【公众号：某某独家分享】曼达洛人第一季完整版",
                "path": "/【公众号：某某独家分享】曼达洛人第一季完整版",
                "type": "directory",
                "total_files": 8,
                "total_size": 8000000000,
                "sub_dirs": [],
                "sub_files": [{"name": "S01E01.mkv", "size": 1000000000}, {"name": "S01E02.mkv", "size": 1000000000}]
            },
            {
                "name": "曼达洛人第1季备份副本",
                "path": "/曼达洛人第1季备份副本",
                "type": "directory",
                "total_files": 8,
                "total_size": 8000000000,
                "sub_dirs": [],
                "sub_files": [{"name": "S01E01.mkv", "size": 1000000000}, {"name": "S01E02.mkv", "size": 1000000000}]
            },
            {
                "name": "三国演义评书全集单田芳版",
                "path": "/三国演义评书全集单田芳版",
                "type": "directory",
                "total_files": 60,
                "total_size": 1200000000,
                "sub_dirs": [],
                "sub_files": [{"name": "001桃园结义.mp3", "size": 20000000}]
            }
        ]
    }

    # 1. 原子判定
    detector = AtomicDetector()
    atomic_units = detector.extract_atomic_units(mock_tree)
    print(f"提取出原子单元: {[u.name for u in atomic_units]}")

    # 2. 查重与名称清洗
    cleaner = DedupAndCleaner()
    unique_units, duplicates = cleaner.process(atomic_units)
    print(f"查重结果: 重复项 {[d['duplicate_path'] for d in duplicates]}")
    print(f"清洗后唯一项: {[u.name for u in unique_units]}")

    # 3. 规划映射（输出至独立测试目录，隔离生产环境规划数据）
    test_out_dir = BASE_DIR / "cache" / "test_output"
    test_out_dir.mkdir(parents=True, exist_ok=True)
    test_csv = test_out_dir / "mock_plan.csv"
    test_md = test_out_dir / "mock_plan.md"
    test_slices = test_out_dir / "slices"

    planner = NetdiskPlanner()
    plan_records = planner.generate_plan(
        unique_units,
        duplicates,
        out_csv=test_csv,
        out_md=test_md,
        slices_dir=test_slices
    )

    print(f"规划记录总数: {len(plan_records)}")
    for r in plan_records:
        print(f"  [{r['action']}] {r['source_path']} -> {r['target_path']} | 原因: {r['reason']}")

    assert test_md.exists(), "测试 Markdown 报告未生成"
    assert test_csv.exists(), "测试 CSV 计划未生成"
    print(">>> 模拟测试验证全部通过！")


if __name__ == "__main__":
    run_mock_test()
