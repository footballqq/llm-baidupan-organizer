import re
import csv
import json
from typing import List, Dict, Any, Optional
from pathlib import Path
from collections import defaultdict

from config import (
    PLAN_MARKDOWN_FILE,
    PLAN_CSV_FILE,
    ISOLATION_ROOT_DIR,
    DELETE_TARGET_DIR,
)
from atomic_detector import AtomicUnit
from hierarchical_classifier import HierarchicalClassifier


class NetdiskPlanner:
    """分类规划器：整合分层智能分类引擎 (HierarchicalClassifier)，负责生成全盘规范化两级层级方案及各类交付物"""

    def __init__(self):
        self.classifier = HierarchicalClassifier()

    def generate_plan(
        self,
        unique_units: List[AtomicUnit],
        duplicates: List[Dict[str, Any]],
        out_csv: Optional[Path] = None,
        out_md: Optional[Path] = None,
        slices_dir: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """生成全盘移动与隔离两级层级化规划方案（自动融合用户历史标注与 delete=x 标记）"""
        print("[Planner] 启动分层智能分类引擎 (A+B 双模驱动) 进行全盘层级规划...")
        target_csv = out_csv or PLAN_CSV_FILE
        target_md = out_md or PLAN_MARKDOWN_FILE
        target_slices = slices_dir or (target_csv.parent / "slices")

        # 1. 预先读取用户已有的历史标注（如 delete=x、自定义 target_path、SKIP 状态等）
        user_annos = self._load_user_annotations(target_csv)
        if user_annos:
            del_count = sum(1 for a in user_annos.values() if a.get("delete", "").strip().lower() == "x")
            print(f"[Planner] 发现历史标注，已载入 {len(user_annos)} 项用户审核记录（其中标记待删除 [delete=x]: {del_count} 项）")

        # 2. 调用分类器执行 A+B 智能两级分类
        plan_records = self.classifier.classify_all(unique_units, duplicates)

        # 3. 完整融合用户标注与待删除标记
        self._apply_user_annotations(plan_records, user_annos)

        # 4. 导出主 CSV、分领域切片 CSV 与 Markdown 报告
        self._export_csv(plan_records, target_csv)
        self._export_slices(plan_records, target_slices)
        self._export_markdown(plan_records, target_md)

        print(f"[Planner] 规划方案已成功生成！")
        print(f"  - Markdown 方案报告: {target_md}")
        print(f"  - 全量执行 CSV 明细表: {target_csv}")
        print(f"  - 分领域切片 CSV 目录: {target_slices}")

        return plan_records

    def refresh_plan_exports(self, csv_path: Path = PLAN_CSV_FILE) -> List[Dict[str, Any]]:
        """从现有 CSV 直接刷新规划表、分领域切片表及方案报告（当用户在 CSV 中修改 delete 或 target_path 时调用，无需重跑大模型）"""
        if not csv_path.exists():
            print(f"[Planner] 错误：未找到规划表文件 {csv_path}")
            return []

        print(f"[Planner] 正在读取并刷新规划表: {csv_path} ...")
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            records = list(reader)

        # 将现有 records 中的用户标记与待删除逻辑进行全面规范化
        delete_name_counts: Dict[str, int] = defaultdict(int)
        for r in records:
            r.setdefault("delete", "")
            r.setdefault("status", "CONFIRMED")
            del_mark = r.get("delete", "").strip().lower()
            if del_mark == "x":
                r["delete"] = "x"
                r["action"] = "DELETE"
                r["category"] = f"{ISOLATION_ROOT_DIR}/03_待删除"
                base_name = r.get("cleaned_name") or Path(r["source_path"]).name
                count = delete_name_counts[base_name]
                delete_name_counts[base_name] += 1
                target_name = base_name if count == 0 else f"{base_name}_副本{count}"
                r["target_path"] = f"{DELETE_TARGET_DIR}/{target_name}"
                old_reason = r.get("reason", "")
                if not old_reason.startswith("用户标记 [delete=x]"):
                    r["reason"] = f"用户标记 [delete=x] 移入待删除目录 (原: {old_reason})"
            else:
                r["delete"] = ""
                # 若之前为 DELETE 但用户去除了 x，则恢复为常规 MOVE
                if r.get("action") == "DELETE":
                    r["action"] = "MOVE"

        # 重新导出全量 CSV、分领域切片 CSV 与 Markdown 报告
        self._export_csv(records)
        self._export_slices(records)
        self._export_markdown(records)

        del_cnt = sum(1 for r in records if r.get("delete") == "x")
        move_cnt = sum(1 for r in records if r.get("action") == "MOVE")
        iso_cnt = sum(1 for r in records if r.get("action") == "ISOLATE")

        print(f"[Planner] 刷新完成！")
        print(f"  - 正常规范移动项: {move_cnt} 项")
        print(f"  - 重复副本隔离项: {iso_cnt} 项")
        print(f"  - 待删除软隔离项: {del_cnt} 项")
        print(f"  - 切片文件更新完毕: {csv_path.parent / 'slices'}")
        print(f"  - Markdown 方案更新完毕: {PLAN_MARKDOWN_FILE}")

        return records

    def _load_user_annotations(self, csv_path: Path = PLAN_CSV_FILE) -> Dict[str, Dict[str, Any]]:
        """从已存在的 CSV 规划表中加载用户的手动标注（如 delete=x、修改的 target_path、SKIP 等），防止重新生成时覆盖"""
        if not csv_path.exists():
            return {}
        annotations = {}
        try:
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    src = row.get("source_path", "").strip()
                    if not src:
                        continue
                    annotations[src] = {
                        "delete": row.get("delete", "").strip(),
                        "target_path": row.get("target_path", "").strip(),
                        "category": row.get("category", "").strip(),
                        "action": row.get("action", "").strip(),
                        "status": row.get("status", "").strip(),
                        "reason": row.get("reason", "").strip(),
                    }
        except Exception as e:
            print(f"[Planner] ⚠️ 加载用户历史标注失败 ({e})，将使用全新规划")
        return annotations

    def _apply_user_annotations(
        self,
        records: List[Dict[str, Any]],
        annotations: Dict[str, Dict[str, Any]]
    ) -> None:
        """将用户标注精准回填到规划记录中，确保 delete=x 移入待删除目录并避免同名冲突"""
        delete_name_counts: Dict[str, int] = defaultdict(int)

        for r in records:
            r.setdefault("delete", "")
            r.setdefault("status", "CONFIRMED")
            src = r.get("source_path", "")
            anno = annotations.get(src, {})

            del_mark = (anno.get("delete") or r.get("delete", "")).strip().lower()
            if del_mark == "x":
                r["delete"] = "x"
                r["action"] = "DELETE"
                r["category"] = f"{ISOLATION_ROOT_DIR}/03_待删除"
                base_name = r.get("cleaned_name") or Path(src).name
                count = delete_name_counts[base_name]
                delete_name_counts[base_name] += 1
                target_name = base_name if count == 0 else f"{base_name}_副本{count}"
                r["target_path"] = f"{DELETE_TARGET_DIR}/{target_name}"
                old_reason = r.get("reason", "")
                if not old_reason.startswith("用户标记 [delete=x]"):
                    r["reason"] = f"用户标记 [delete=x] 移入待删除目录 (原: {old_reason})"
            else:
                r["delete"] = ""
                # 继承用户自定义的 target_path（非隔离删除路径）
                if anno.get("target_path") and not anno.get("target_path", "").startswith(f"/{ISOLATION_ROOT_DIR}/03_待删除"):
                    r["target_path"] = anno["target_path"]
                if anno.get("category") and not anno.get("category", "").startswith(f"{ISOLATION_ROOT_DIR}/03_待删除"):
                    r["category"] = anno["category"]
                if anno.get("action") and anno.get("action") != "DELETE":
                    r["action"] = anno["action"]

            # 继承用户审核状态（如 SKIP, COMPLETED 等）
            if anno.get("status"):
                r["status"] = anno["status"]

    def _export_slices(self, records: List[Dict[str, Any]], out_dir: Optional[Path] = None) -> None:
        """导出分领域的切片 CSV 文件，方便用户按模块分别查看与审核，避免单表过大"""
        slices_dir = out_dir or (PLAN_CSV_FILE.parent / "slices")
        slices_dir.mkdir(parents=True, exist_ok=True)

        slice_mapping = {
            "01_小学数学_竞赛与常规.csv": lambda r: "数学" in r.get("category", "") and r.get("action") == "MOVE",
            "02_小学语文与英语.csv": lambda r: ("语文" in r.get("category", "") or "英语" in r.get("category", "")) and r.get("action") == "MOVE",
            "03_少儿通识与素养.csv": lambda r: "少儿通识与素养" in r.get("category", "") and r.get("action") == "MOVE",
            "04_儿童听读与娱乐.csv": lambda r: "儿童听读与娱乐" in r.get("category", "") and r.get("action") == "MOVE",
            "05_工作研报与经管学术.csv": lambda r: "工作与研究" in r.get("category", "") and r.get("action") == "MOVE",
            "06_影视影音与音频素材.csv": lambda r: "影视与音像" in r.get("category", "") and r.get("action") == "MOVE",
            "07_个人生活_相册与工具.csv": lambda r: "个人生活与备份" in r.get("category", "") and r.get("action") == "MOVE",
            "08_建议清理隔离区.csv": lambda r: r.get("action") in ("ISOLATE", "DELETE") or r.get("delete", "").strip().lower() == "x",
            "09_其他待复核.csv": lambda r: ("其他待复核" in r.get("category", "") or "其他未归类" in r.get("category", "")) and r.get("action") == "MOVE"
        }

        fieldnames = ["id", "source_path", "action", "category", "cleaned_name", "target_path", "reason", "status", "delete"]
        for fname, predicate in slice_mapping.items():
            matched = [r for r in records if predicate(r)]
            out_path = slices_dir / fname
            with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in matched:
                    row_data = {k: r.get(k, "") for k in fieldnames}
                    writer.writerow(row_data)

    def _export_csv(self, records: List[Dict[str, Any]], out_csv: Optional[Path] = None) -> None:
        """导出为 UTF-8-SIG 的 CSV 供用户在 Excel 中直接编辑查看"""
        target_path = out_csv or PLAN_CSV_FILE
        target_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["id", "source_path", "action", "category", "cleaned_name", "target_path", "reason", "status", "delete"]
        with open(target_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in records:
                row_data = {k: r.get(k, "") for k in fieldnames}
                writer.writerow(row_data)

    def _export_markdown(self, records: List[Dict[str, Any]], out_md: Optional[Path] = None) -> None:
        """导出 Markdown 汇总方案报告 (含两级金字塔结构统计与隔离/删除明细)"""
        target_md = out_md or PLAN_MARKDOWN_FILE
        target_md.parent.mkdir(parents=True, exist_ok=True)

        moves = [r for r in records if r.get("action") == "MOVE" and r.get("delete", "").strip().lower() != "x"]
        duplicates = [r for r in records if r.get("action") == "ISOLATE" and r.get("delete", "").strip().lower() != "x"]
        deletes = [r for r in records if r.get("action") == "DELETE" or r.get("delete", "").strip().lower() == "x"]
        total_isolated = len(duplicates) + len(deletes)

        # 按大领域聚合子系列统计
        hierarchy: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        for m in moves:
            cat = m.get("category", "").strip("/")
            if "/" in cat:
                domain, subtier = cat.rsplit("/", 1)
            else:
                domain, subtier = cat, "综合系列"
            hierarchy[domain][subtier].append(m.get("cleaned_name", ""))

        md_content = [
            "# 百度网盘智能整理与两级层级化归档方案报告\n",
            "> [!NOTE]",
            f"> 本方案采用 **A+B 协同架构**（my-fast-gptoss 语义提炼 + Kev 本地极速决策）生成，并已完整融合**用户人工审核与删除标记**。",
            f"> 共梳理出 **{len(records)}** 个操作单元：",
            f"> - **两级分类规范移动项**：{len(moves)} 项（大领域 -> 二级细分子系列 -> 资源目录）",
            f"> - **隔离与待删除管理项**：{total_isolated} 项（100% 安全软隔离移动至 `/{ISOLATION_ROOT_DIR}/`，绝不物理删除）",
            f">   - **确凿重复副本**：{len(duplicates)} 项（移入 `/{ISOLATION_ROOT_DIR}/01_确凿重复副本/`）",
            f">   - **用户标记待删除**：{len(deletes)} 项（根据用户 `[delete=x]` 标记，统一移入 `{DELETE_TARGET_DIR}/`）\n",
            "## 1. 目标归档两级金字塔体系与资源数量统计\n",
            "| 一级大领域 | 二级细分子系列 | 资源数 | 典型资源示例 |",
            "| :--- | :--- | :--- | :--- |"
        ]

        for domain in sorted(hierarchy.keys()):
            subtiers = hierarchy[domain]
            domain_total = sum(len(items) for items in subtiers.values())
            for idx, (subtier, items) in enumerate(sorted(subtiers.items(), key=lambda x: len(x[1]), reverse=True)):
                dom_label = f"**{domain}** ({domain_total})" if idx == 0 else ""
                sample_str = "、".join(items[:2])
                md_content.append(f"| {dom_label} | `{subtier}` | {len(items)} | {sample_str} |")

        # 隔离与待删除统计
        md_content.append(f"| **待清理隔离区** | `01_确凿重复副本` | {len(duplicates)} | 与原版完全一致的重复冗余，隔离安全存放 |")
        md_content.append(f"| **待清理隔离区** | `03_待删除` | {len(deletes)} | 用户在表格中标记 [delete=x] 的待清理资源 |")

        md_content.extend([
            "\n---\n",
            "## 2. 隔离与待删除明细（零误删保障）\n",
            "> [!IMPORTANT]",
            f"> 以下内容均将统一移至网盘 `/{ISOLATION_ROOT_DIR}/` 目录，绝对不执行硬删除，用户可在网盘中二次核实：\n",
            "| 序号 | 原始路径 | 操作类型 | 隔离/删除原因 | 目标归类路径 |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ])

        def _esc(val: Any) -> str:
            return str(val).replace("|", "\\|").replace("\n", " ")

        # 汇总展示隔离与删除项
        all_isolated = duplicates + deletes
        for iso in all_isolated:
            act_label = "【待删除】" if (iso.get("action") == "DELETE" or iso.get("delete") == "x") else "【重复副本】"
            md_content.append(f"| {iso.get('id', '')} | `{_esc(iso.get('source_path', ''))}` | {act_label} | {_esc(iso.get('reason', ''))} | `{_esc(iso.get('target_path', ''))}` |")

        md_content.extend([
            "\n---\n",
            "## 3. 两级层级归类移动明细预览（前 35 项）\n",
            "| 序号 | 原始路径 | 规范化命名 | 目标归类路径 | 归类理由 |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ])

        for m in moves[:35]:
            md_content.append(f"| {m.get('id', '')} | `{_esc(m.get('source_path', ''))}` | **{_esc(m.get('cleaned_name', ''))}** | `{_esc(m.get('target_path', ''))}` | {_esc(m.get('reason', ''))} |")

        if len(moves) > 35:
            md_content.append(f"\n*(其余 {len(moves) - 35} 项请在 `organize_plan.csv` 或 `slices/` 分领域切片表中查看完整明细)*\n")

        md_content.extend([
            "\n---\n",
            "## 4. 人工审核与微调指南\n",
            "1. **标记待删除**：在 `organize_plan.csv` 或 `slices/` 下任意切片表的 `delete` 列中填入 `x`，运行 `python run_pipeline.py refresh-plan` 即可自动移入 `03_待删除` 目录；",
            "2. **调整归类**：若对某项的归类或子系列不满意，可直接在表格中修改 `target_path` 列；",
            "3. **跳过不移动**：若某项希望暂时保持原位不动，将对应行的 `status` 改为 `SKIP`；",
            "4. **同步切片与报告**：编辑并保存 CSV 后，运行以下命令刷新：\n",
            "   ```powershell",
            "   python run_pipeline.py refresh-plan",
            "   ```\n",
            "5. **受控执行与回滚**：\n",
            "   ```powershell",
            "   # 1. 仿真预览（不移动任何文件）",
            "   python run_pipeline.py execute --dry-run",
            "   # 2. 正式执行（受控移动，带同名保护与断点自愈）",
            "   python run_pipeline.py execute",
            "   # 3. 如需一键原路撤销",
            "   python run_pipeline.py undo",
            "   ```\n"
        ])

        with open(target_md, "w", encoding="utf-8") as f:
            f.write("\n".join(md_content))
