import re
import json
from typing import Dict, Any, List, Set, Tuple
from dataclasses import dataclass, field, asdict
from openai import OpenAI

from config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, parse_llm_json

# 常见的高内聚子目录命名模式（同构序列，代表该目录是一套完整资源）
COHESIVE_SUBDIR_PATTERNS = [
    r"^\d{4}$",                     # 年份如 2001, 2002, 2023
    r"^amc\s*\d+",                  # amc2001, amc8-2022
    r"[0-9一二三四五六七八九十百]+[讲课集章期节卷周级阶篇册单元专题季]", # 第1讲, 第02集, 10期, 第03周, 1单元, 第1专题, 第一季
    r"(s\d+|season\s*\d+|ep?\d+)",  # s01, season 1, ep01
    r"^[上下]册$",                   # 上册, 下册
    r"(基础|提高|冲刺|真题|讲义|课件|答案|视频|音频|解析|试卷|配套|练习册|作业|素材|源码|答疑|测试|复习|拓展|超越|兴趣)篇?",
    r"(unit|level|grade|stage|step|class|week|day|lesson|test|part)\s*[a-z0-9_-]+",
    r"^[1-6一二三四五六七八九]年级([上下]册?)?",
    r"(春季|暑假|秋季|寒假|强化|冲刺|真题|提高|培优|超越|拓展|兴趣)班?",
    r"^(pdf|mp3|mp4|mkv|flac|avi|doc|docx|ppt|pptx)$",
    r"^\d{1,3}$",                   # 纯数字序号 01, 02
    r"^\d+\s*[-~_]\s*\d+$",         # 4-4, 5-5 等数独/题册分段
    r"^[第]?[0-9一二三四五六七八九十]+级[别段]" # 第一级别, 第二级别
]

# 典型的顶层松散容器特征词（需拆解分发）
GENERIC_CONTAINERS = {
    "root", "学习", "学习资料", "资料", "网盘资料", "网盘备份", "各种资料",
    "各类资源", "我的资源", "新建文件夹", "文档", "视频", "杂项", "其他", "download",
    "baidunetdisk", "小学", "初中", "高中", "幼小衔接", "小初高",
    "baiduq", "aa育儿", "work", "temp", "memory", "mobile", "apps", "yasuo", "aaatemp",
    "我的文档", "我的视频", "我的照片", "我的音乐", "share", "xbla", "xbox", "百度云解压", "微信备份",
    "dcim", "pictures", "camera", "backup", "音频备份", "控笔", "鸡娃"
}

# 明确指示独立成套原子资源的标题关键词（绝不拆散）
ATOMIC_TITLE_KEYWORDS = [
    "完结", "全套", "全集", "合集", "大合集", "大全", "课本", "系列", "教程", "精读",
    "讲义", "真题", "凯叔", "高思", "学而思", "口袋神探", "神奇图书馆", "字母积木",
    "sasmo", "amc", "牛津树", "思泉", "钱儿爸", "新概念", "raz", "海尼曼", "加州",
    "窦神", "诸葛学堂", "巨人", "迎春杯", "走美杯", "希望杯", "华杯赛", "袋鼠数学",
    "scratch", "python", "编程", "奥数", "自然拼读", "分级阅读", "巧虎",
    "lego", "乐高", "大猫", "大英", "国家地理", "乐乐课堂", "包", "卡片", "百科",
    "xuersi", "qiushi", "《", "【", "（全）", "season", "季", "集", "册", "讲",
    "报告", "研报", "调研", "公开课", "通史", "常青藤", "画啦啦", "螺丝钉", "数学城",
    "万物运转", "洋葱数学", "write source", "罗博深", "叫叫阅读", "摩比", "兰登",
    "公文", "姜天一", "培生", "super simple songs"
]


@dataclass
class AtomicUnit:
    """原子资源单元数据结构"""
    path: str
    name: str
    parent_path: str
    total_files: int
    total_size: int
    sample_files: List[str] = field(default_factory=list)
    sample_subdirs: List[str] = field(default_factory=list)
    cohesion_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AtomicDetector:
    """原子资源单元与容器目录识别器"""

    def __init__(self):
        self.client = OpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY
        )

    def extract_atomic_units(self, tree: Dict[str, Any]) -> List[AtomicUnit]:
        """从根节点开始遍历，识别所有不可拆分的原子资源单元（迭代多轮处理嵌套歧义）"""
        print("[AtomicDetector] 正在通过规则启发式与语义理解判定原子目录单元...")
        atomic_units: List[AtomicUnit] = []
        ambiguous_nodes: List[Dict[str, Any]] = []

        self._evaluate_node(tree, atomic_units, ambiguous_nodes, depth=0)

        # 若有歧义节点，尝试微批次仲裁；若异常或超时，安全保全为原子单元（绝不粉碎）
        if ambiguous_nodes:
            print(f"[AtomicDetector] 发现 {len(ambiguous_nodes)} 个待仲裁目录，优先语义判定...")
            try:
                llm_decisions = self._batch_llm_judge(ambiguous_nodes)
            except Exception as e:
                print(f"[AtomicDetector] LLM 批量裁决跳过 ({e})，全量安全保留为独立单元。")
                llm_decisions = {}

            for node in ambiguous_nodes:
                decision = llm_decisions.get(node["path"], "ATOMIC_UNIT")
                if decision == "CONTAINER":
                    for child in node.get("sub_dirs", []):
                        self._evaluate_node(child, atomic_units, [], depth=2)
                else:
                    atomic_units.append(self._make_atomic_unit(node, "判定为独立成套资源单元"))

        print(f"[AtomicDetector] 原子单元识别完成！共提取出 {len(atomic_units)} 个独立资源单元。")
        return atomic_units

    def _evaluate_node(
        self,
        node: Dict[str, Any],
        atomic_units: List[AtomicUnit],
        ambiguous_nodes: List[Dict[str, Any]],
        depth: int = 0
    ) -> None:
        """评估单个节点"""
        path = node.get("path", "")
        name = node.get("name", "").strip()
        sub_dirs = node.get("sub_dirs", [])
        sub_files = node.get("sub_files", [])

        clean_name = name.strip().lower()

        # 根目录或泛义容器强制向下拆解
        if depth == 0 or clean_name in GENERIC_CONTAINERS or clean_name.startswith("来自：") or path in ("/", ""):
            for child in sub_dirs:
                self._evaluate_node(child, atomic_units, ambiguous_nodes, depth=depth + 1)
            return

        # 1. 标题命中高内聚成套特征词，直接判定为独立原子单元
        if any(kw in clean_name for kw in ATOMIC_TITLE_KEYWORDS):
            atomic_units.append(self._make_atomic_unit(node, "标题特征词高内聚命中"))
            return

        # 2. 叶子目录（没有子目录，只有文件）直接作为原子单元
        if not sub_dirs:
            if sub_files:
                atomic_units.append(self._make_atomic_unit(node, "叶子目录且包含文件"))
            return

        # 3. 检查子目录是否呈现高内聚序列性（例如年份、讲次、剧集）
        child_names = [d.get("name", "").strip().lower() for d in sub_dirs]
        match_count = sum(1 for cname in child_names if any(re.search(pat, cname) for pat in COHESIVE_SUBDIR_PATTERNS))

        if match_count > 0 and (match_count / len(child_names)) >= 0.3:
            atomic_units.append(self._make_atomic_unit(node, f"子目录高内聚 ({match_count}/{len(child_names)})"))
            return

        # 4. 处于深层（已穿透顶层容器分类）的成套资源，直接保留为独立原子单元
        if depth >= 2:
            atomic_units.append(self._make_atomic_unit(node, "深层结构独立资源单元"))
            return

        # 5. 顶层有多子目录且结构不明确的，放入轻量仲裁队列
        if len(sub_dirs) >= 2:
            ambiguous_nodes.append(node)
        else:
            atomic_units.append(self._make_atomic_unit(node, "单子目录独立单元"))

    def _make_atomic_unit(self, node: Dict[str, Any], reason: str) -> AtomicUnit:
        """构建 AtomicUnit 对象"""
        path = node.get("path", "")
        name = node.get("name", "")
        parent_path = "/" + "/".join([p for p in path.strip("/").split("/")[:-1] if p])
        sub_files = [f.get("name", "") for f in node.get("sub_files", [])][:10]
        sub_dirs = [d.get("name", "") for d in node.get("sub_dirs", [])][:10]

        return AtomicUnit(
            path=path,
            name=name,
            parent_path=parent_path,
            total_files=node.get("total_files", 0),
            total_size=node.get("total_size", 0),
            sample_files=sub_files,
            sample_subdirs=sub_dirs,
            cohesion_reason=reason
        )

    def _batch_llm_judge(self, nodes: List[Dict[str, Any]], batch_size: int = 5) -> Dict[str, str]:
        """批量微批次（5项一组）请求大模型裁决目录性质，避免上下文过长"""
        decisions: Dict[str, str] = {}

        for i in range(0, len(nodes), batch_size):
            batch = nodes[i:i + batch_size]
            items_desc = []
            for item in batch:
                cnames = [d.get("name", "") for d in item.get("sub_dirs", [])][:4]
                fnames = [f.get("name", "") for f in item.get("sub_files", [])][:2]
                items_desc.append({
                    "path": item["path"],
                    "folder_name": item["name"],
                    "sample_subdirs": cnames,
                    "sample_files": fnames
                })

            prompt = (
                "判断以下目录类型（二选一）：\n"
                "1. 'ATOMIC_UNIT'：成套不可分割内容（如成套课程、剧集、专题，应整体移动）\n"
                "2. 'CONTAINER'：松散大容器（包含不同科目或杂项，应拆开）\n\n"
                f"目录：\n{json.dumps(items_desc, ensure_ascii=False, indent=2)}\n\n"
                "严格输出 JSON：\n"
                "{\n"
                "  \"/路径\": \"ATOMIC_UNIT\" 或 \"CONTAINER\"\n"
                "}"
            )

            try:
                resp = self.client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a file classifier. Output strictly valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    timeout=12.0
                )
                raw_text = resp.choices[0].message.content or ""
                parsed = parse_llm_json(raw_text)
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        norm_k = "/" + k.strip().strip("/")
                        decisions[norm_k] = v
                        decisions[k.strip()] = v
            except Exception as e:
                print(f"[AtomicDetector] LLM 仲裁超时或不可达 ({e})，安全保留为 ATOMIC_UNIT。")
                for item in batch:
                    decisions[item["path"]] = "ATOMIC_UNIT"

        return decisions
