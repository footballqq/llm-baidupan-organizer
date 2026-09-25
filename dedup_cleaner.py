import re
import json
from typing import List, Dict, Any, Tuple
from collections import defaultdict
from openai import OpenAI

from config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, parse_llm_json
from atomic_detector import AtomicUnit

# 广告与引流水印模式
AD_PATTERNS = [
    r"【.*?(?:公众号|微信|分享|首发|无密|整理|独家|精选|免费|推荐).*?】",
    r"\[.*?(?:公众号|微信|分享|首发|无密|整理|独家|精选|免费|推荐).*?\]",
    r"（.*?(?:公众号|微信|分享|首发|无密|整理|独家|精选|免费|推荐).*?）",
    r"\(.*?(?:公众号|微信|分享|首发|无密|整理|独家|精选|免费|推荐).*?\)",
    r"^\d+[\.、\-\_\s]+",        # 剥离诸如 '01. ', '001-' 前缀
    r"[\-_]?(?:高清|1080p|720p|4k|超清|无水印|完整版|未删减)$",
    r"【完结】",
    r"【全套】",
    r"【最新】"
]


class DedupAndCleaner:
    """智能查重与目录名称规范化净化引擎"""

    def __init__(self):
        self.client = OpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY
        )

    def process(self, units: List[AtomicUnit]) -> Tuple[List[AtomicUnit], List[Dict[str, Any]]]:
        """执行查重与目录名清洗"""
        print("[DedupCleaner] 开始执行全盘原子单元指纹查重与命名规范化...")
        
        # 1. 精准指纹查重
        unique_units, duplicates = self._find_duplicates(units)
        print(f"[DedupCleaner] 查重完成: 独立单元 {len(unique_units)} 个，检出确凿重复副本 {len(duplicates)} 个。")

        # 2. 批量名称清洗与规范化
        cleaned_units = self._clean_names(unique_units)

        return cleaned_units, duplicates

    def _find_duplicates(self, units: List[AtomicUnit]) -> Tuple[List[AtomicUnit], List[Dict[str, Any]]]:
        """基于文件总数、总大小以及核心文件列表特征检测重复资源"""
        fingerprint_map: Dict[str, List[AtomicUnit]] = defaultdict(list)

        for u in units:
            if u.total_files == 0 or u.total_size == 0:
                fingerprint_map[f"empty_{u.path}"].append(u)
                continue

            # 过滤掉推广文本（如 '关注公众号.txt', '更多资源.url' 等），提取核心文件特征
            core_files = sorted([
                f for f in u.sample_files
                if not any(k in f for k in ["公众号", "网址", "解压密码", "宣讲", "宣传", "更多资源"])
            ])
            core_subdirs = sorted(u.sample_subdirs[:3])
            # 构建结构指纹：文件数:总大小:核心文件名:核心子目录名（多维度防碰撞）
            fp = f"{u.total_files}:{u.total_size}:{','.join(core_files[:3])}:{','.join(core_subdirs)}"
            fingerprint_map[fp].append(u)

        unique_units: List[AtomicUnit] = []
        duplicates: List[Dict[str, Any]] = []

        for fp, group in fingerprint_map.items():
            if len(group) == 1:
                unique_units.append(group[0])
            else:
                # 排序选出路径层级最规范/最短的一项作为保留正本
                group.sort(key=lambda x: (len(x.path), x.path))
                canonical = group[0]
                unique_units.append(canonical)

                for dup in group[1:]:
                    duplicates.append({
                        "duplicate_path": dup.path,
                        "duplicate_name": dup.name,
                        "canonical_path": canonical.path,
                        "total_files": dup.total_files,
                        "total_size": dup.total_size,
                        "reason": f"与正本 [{canonical.path}] 文件数与大小完全一致，确认为重复副本"
                    })

        return unique_units, duplicates

    def _clean_names(self, units: List[AtomicUnit]) -> List[AtomicUnit]:
        """清洗目录名称，去除广告引流水印并标准化"""
        # 第一轮：正则规则速洗
        for u in units:
            cleaned = u.name
            for pat in AD_PATTERNS:
                cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
            cleaned = cleaned.strip(" -_[]【】()（）")
            if cleaned:
                u.name = cleaned

        # 第二轮：抽取极度杂乱无意义（如以“新建文件夹”或“未命名”开头）的项由大模型轻量润色
        messy_units = [
            u for u in units
            if u.name.startswith("新建文件夹") or u.name.startswith("未命名")
        ]

        if messy_units:
            print(f"[DedupCleaner] 发现 {len(messy_units)} 个命名随意或过长的目录，调用 LLM 进行规范化润色...")
            norm_names = self._llm_normalize_names(messy_units)
            for u in messy_units:
                if u.path in norm_names:
                    u.name = norm_names[u.path]

        return units

    def _llm_normalize_names(self, units: List[AtomicUnit], batch_size: int = 5) -> Dict[str, str]:
        """微批次切片调用大模型为杂乱目录提供清晰、学术规范的名称（每批5项，极简提示词）"""
        results: Dict[str, str] = {}

        for i in range(0, len(units), batch_size):
            batch = units[i:i + batch_size]
            items = []
            for u in batch:
                items.append({
                    "path": u.path,
                    "raw_name": u.name,
                    "sample_files": u.sample_files[:2]
                })

            prompt = (
                "请对以下5个网盘目录进行名称规范化与去广告净化：\n"
                "1. 剔除宣传引流、公众号、免责声明等无用信息。\n"
                "2. 结合样本文件名生成简洁标准的名称（如：'AMC8历年真题及解析'、'高思数学三年级精讲课程'）。\n\n"
                f"{json.dumps(items, ensure_ascii=False, indent=2)}\n\n"
                "严格输出 JSON（键为 path，值为规范化名称）：\n"
                "{\n"
                "  \"/路径1\": \"规范化名称1\"\n"
                "}"
            )

            try:
                resp = self.client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a professional file metadata normalization assistant. Output strictly valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    timeout=10.0
                )
                raw_text = resp.choices[0].message.content or ""
                parsed = parse_llm_json(raw_text)
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        norm_k = "/" + k.strip().strip("/")
                        results[norm_k] = str(v).strip()
                        results[k.strip()] = str(v).strip()
            except Exception as e:
                print(f"[DedupCleaner] LLM 名称润色超时或异常 ({e})，保留规则清洗后的名称。")

        return results

