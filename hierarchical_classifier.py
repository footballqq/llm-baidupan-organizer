import re
import os
import json
import time
from typing import Dict, Any, List, Tuple, Optional
from collections import defaultdict
from pathlib import Path
from openai import OpenAI

from config import (
    SUBTIER_CACHE_FILE,
    CLASSIFICATION_LOG_FILE,
    LLM_BASE_URL,
    LLM_API_KEY,
    LLM_MODEL,
    parse_llm_json,
)
from kev_client import KevClient
from atomic_detector import AtomicUnit

# 预设各大领域的标准二级子系列框架与判定参考（用于 Kev 与 LLM 指导）
STANDARD_SUBTIERS = {
    "01_孩子学习/小学数学_竞赛": {
        "高思数学系列": "高思竞赛课本、高思导引、高思VIP课",
        "学而思奥数系列": "学而思大白本、学而思大白皮、小学奥数全套、学而思X班",
        "AMC8竞赛系列": "AMC8历年真题及解析、核心高频考点、AMC讲义",
        "袋鼠数学系列": "袋鼠数学各Level真题解析、中英文试卷",
        "综合杯赛与思维": "迎春杯、希望杯、华杯赛、走美杯、DSS大师赛、数独、思维训练、数学大王、SASMO、早培"
    },
    "01_孩子学习/小学数学_常规": {
        "公文与新加坡数学": "公文数学全套、新加坡数学教材与练习",
        "计算与口算速算": "口算大卡、计算小超市、核心概念练习、速算珠心算",
        "教材课本与同步练": "人教版、北师大、苏教版课本与课课练、乐乐课堂",
        "期末真题与综合测试": "各区期中期末真题卷、单元测验卷"
    },
    "01_孩子学习/小学语文": {
        "思泉大语文系列": "思泉大语文1-9年级全套课程与讲义",
        "窦神与诸葛学堂": "窦神大语文、文言文必考专题、名师语文",
        "汉字识字与字帖控笔": "好字在·字有道理、字成方圆、控笔字帖训练、书法字帖",
        "国学古诗与文言文": "三字经、弟子规、千字文、声律启蒙、名家古诗文",
        "阅读与作文写作营": "小作家作文、看图写话、阅读理解精读、一亩中文"
    },
    "01_孩子学习/小学英语": {
        "牛津树分级系列": "牛津树1-14全套、点读包与精读解析",
        "Raz原版分级阅读": "Reading A-Z全级别绘本、词汇汇总",
        "剑桥考级与真题": "KET、PET真题及Trainer",
        "新概念与语法体系": "新概念英语、Sap Learning Grammar、语法精讲",
        "原版绘本与名著名篇": "Sherlock Holmes、Dragon Master、汪培珽书单、原版有声书、动画绘本"
    },
    "02_儿童听读与娱乐": {
        "凯叔讲故事系列": "凯叔西游记、三国演义、水浒传、封神榜、365夜、神奇图书馆",
        "钱儿爸系列": "不一样的卡梅拉全集、钱儿爸经典名著有声书",
        "传统评书与名著曲艺": "单田芳评书全集、传统名著评书",
        "英文原版动画与听读": "小鼠波波Maisy、小猪佩奇、本和霍利、Raa Raa小狮子、Blippi",
        "成语故事与睡前童话": "大话成语300回、奶泡泡成语、宫西达也绘本音频、汉声童话"
    },
    "03_工作与研究": {
        "量化与金工多因子": "Alpha 101, GTJA 191, 招商金工因子合集, 因子模型框架、HFT、R量化",
        "期权基差与高频策略": "50ETF期权专题、择时系列、股指期货基差工具、高频交易、停牌套利",
        "券商研报与宏观策略": "中信证券、申万宏源、行业研究报告、调研PPT、金融科技趋势",
        "经管人文与社科精读": "罗辑思维精选读书会、德鲁克管理学、中国近代史、经济学文献、通识名著",
        "学术文献与机器学习": "机器学习专题、APAMA案例、学术论文"
    },
    "01_孩子学习/少儿通识与素养": {
        "少儿编程与机器人": "Python少儿编程、Scratch趣味编程、EV3机器人",
        "科普百科与智力启蒙": "DK儿童百科、神奇校车、万物运转、三阶魔方、数学的故事",
        "少儿美育与书法": "画啦啦少儿美术、爱豆毕加索艺术课、手工折纸粘土、国画水彩、围棋快易精",
        "少儿历史与地理": "国家地理双语卡片、你好呀故宫、讲给孩子的中国历史地理、中国通史",
        "少儿体育与体能健康": "常爸跳绳训练营、少儿体能课、体态运动评测",
        "家庭教育与育儿方法": "正面管教、家庭育儿指南、儿童时间管理、儿童发展测评表"
    },
    "04_影视与音像": {
        "经典美剧与剧集": "傲骨贤妻、曼达洛人、黑袍纠察队、高清剧集",
        "高清电影与纪录片": "TED演讲、精选纪录片、蓝光电影",
        "音乐音频与音效素材": "个人音乐、Vlog音效素材、loops配乐、助眠冥想、歌谱合集"
    },
    "05_个人生活与备份": {
        "家庭相册与相机拍摄": "手机相机摄影原片、旅游照片、DCIM、家庭写真",
        "系统软件与装机工具": "Clover、TotalCommander、Win11镜像、游戏工具、NDS模拟器"
    },
    "06_其他待复核": {
        "综合与待人工复核": "未明确归属的杂项资源、零散文件与特殊压缩包"
    }
}


class HierarchicalClassifier:
    """两级分层分类与智能聚类引擎 (A+B 双模驱动: my-fast-gptoss + 本地 Kev 0.5B)"""

    def __init__(self):
        self.client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
        self.kev = KevClient()
        self.cache: Dict[str, str] = self._load_cache()
        self._init_logger()

    def _init_logger(self):
        CLASSIFICATION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = open(CLASSIFICATION_LOG_FILE, "a", encoding="utf-8")
        self._log("=" * 60)
        self._log(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 分层分类引擎初始化启动")
        self._log(f"  - 大模型配置: {LLM_MODEL} @ {LLM_BASE_URL}")
        self._log(f"  - 本地 Kev 状态: {'在线 (0.5B)' if self.kev.is_online() else '离线 (自动使用大模型)'}")

    def _log(self, msg: str):
        print(msg)
        self.log_file.write(msg + "\n")
        self.log_file.flush()

    def _load_cache(self) -> Dict[str, str]:
        if SUBTIER_CACHE_FILE.exists():
            try:
                with open(SUBTIER_CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        tmp_file = SUBTIER_CACHE_FILE.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, SUBTIER_CACHE_FILE)
        except Exception as e:
            self._log(f"[CacheError] 保存细分子类断点快照失败: {e}")

    def get_unit_context(self, u: AtomicUnit) -> str:
        """多维上下文提炼：彻底解析无意义数字、vol、简写目录"""
        # 过滤广告杂质文件，挑出具有语义特征的核心文件
        core_files = [
            f for f in u.sample_files
            if not any(k in f.lower() for k in ["公众号", "解压密码", "宣讲", "网址", "交流群", "下载说明"])
        ]
        # 挑选指示文件类型与内容的代表性文件
        valid_files = [f for f in core_files if any(ext in f.lower() for ext in [".pdf", ".mp4", ".mp3", ".mkv", ".doc", ".xls", ".7z", ".rar"])]
        if not valid_files:
            valid_files = core_files

        context_parts = [f"目录名: {u.name}", f"网盘路径: {u.path}"]
        if valid_files:
            context_parts.append(f"包含文件样本: {valid_files[:3]}")
        if u.sample_subdirs:
            context_parts.append(f"子目录样本: {u.sample_subdirs[:3]}")

        return " | ".join(context_parts)

    def assign_domain(self, u: AtomicUnit) -> str:
        """一级大领域分类 (修复封面图误判，结合完整路径与核心文件语义)"""
        n = u.name.lower()
        p = u.path.lower()
        ctx = self.get_unit_context(u).lower()
        c = f"{n} {p} {ctx}"

        # 0. 建议隔离判定 (排除多学段全套)
        is_isolated = self._check_deletion_candidate(u)
        if is_isolated:
            return "_待清理隔离区/超龄与废弃资源"

        # 1. 明确的影视与音频素材
        if "/我的视频" in p or any(k in c for k in ["美剧", "剧集", "纪录片", "1080p", "720p", "bdrip", "mkv", "曼达洛人", "圣殿春秋", "yes minister", "the boys", "the.boys", "陈情令", "good wife", "goodwife", "博士2", "傲骨贤妻", "龙珠", "脱口秀", "劣迹"]):
            return "04_影视与音像"
        if "/我的音乐" in p or any(k in c for k in ["音效", "bgm", "配乐", "loops", "vol-", "sound_fx", "vlog", "拟声", "纯音乐", "背景音乐", "冥想", "助眠", "歌谱", "视频号素材"]):
            return "04_影视与音像"

        # 2. 个人生活、相册、手机备份与工具
        if any(k in p for k in ["/来自：", "/来自:", "/memory/", "/mobile/", "/屏幕截图", "/apps", "/xbox", "/xbla"]) or any(k in c for k in ["我的照片", "相册", "dcim", "花家怡园", "jingyuetan", "刘新华", "baixiaoying", "judy", "total commander", "totalcmd", "clover", "xyplorer", "win11", "office", "apk", "安装包", "驱动", "rom", "iso", "nds", "gba", "airpin", "抠图", "san11", "contacts", "通讯录", "园博园", "写真", "修图", "小丁当", "齐中赫", "生活用品", "mama"]):
            return "05_个人生活与备份"

        # 3. 工作研报与学术金融 / 经管读书会
        if "/work" in p or any(k in c for k in ["研报", "论文", "调研", "证券", "券商", "基金", "因子", "金工", "高频", "基差", "期权", "择时", "选股", "机器学习", "瑞晖泽金", "技术指标", "宏观", "策略", "财报", "jpm", "morgan", "sarao", "apama", "alpha 101", "gtja", "etf", "fintech", "金融科技", "互联网银行", "数理经济", "计量经济", "伍德里奇", "woodridge", "停牌套利", "宽客", "quant", "hft", "从众危机", "魔鬼经济学", "国泰君安", "r量化", "机构配置"]):
            return "03_工作与研究"
        if "/我的文档" in p and ("第" in n or "期" in n):
            return "03_工作与研究"

        # 4. 小学语文
        if any(k in c for k in ["大语文", "窦神", "思泉", "巨人语文", "作文", "阅读", "生字", "字帖", "古诗", "文言文", "国学", "控笔", "字词", "看图写话", "河elephant", "字有道理", "泉灵语文", "声律启蒙", "成语", "一亩中文", "字成方圆", "好字在", "书法", "中国年"]):
            return "01_孩子学习/小学语文"

        # 5. 小学英语
        if any(k in c for k in ["raz", "海尼曼", "牛津树", "oxford", "ket", "pet", "新概念", "phonics", "reading", "english", "盖兆泉", "分级阅读", "培生", "biff", "nate", "汪培珽", "廖彩杏", "trucktown", "sight word", "grammar", "dragon master", "sherlock", "little bear", "super simple songs", "kids abc", "语感启蒙", "跟小小孩说英文", "美式全学科英语", "i can read", "wheels on the bus", "good night, gorilla", "very hungry caterpillar", "panda bear", "斑马英语", "journey to the west", "语音训练", "英语学霸", "曹文"]):
            return "01_孩子学习/小学英语"

        # 6. 儿童听读与故事娱乐
        if any(k in c for k in ["凯叔", "钱儿爸", "钱爸", "西游记", "三国", "水浒", "封神", "睡前故事", "评书", "单田芳", "小灯塔", "奶泡泡", "珀利", "动画片", "小猪佩奇", "小鼠波波", "maisy", "螺丝钉", "米奇妙妙", "numberblocks", "本和霍利", "大话成语", "宫西达也", "汉声童话", "猴子先生", "raa raa", "blippi"]):
            return "02_儿童听读与娱乐"

        # 7. 小学数学竞赛
        if any(k in c for k in ["amc", "高思", "导引", "奥数", "希望杯", "xwb", "迎春杯", "走美杯", "袋鼠", "数独", "大白本", "大白皮", "91好课", "华杯", "dss", "大师赛", "思维训练", "数学大王", "希望数学", "学而思x班", "学而思创新", "学而思杯", "七大能力", "xesx", "sasmo", "math league", "大联盟", "早培", "孙佳俊"]):
            return "01_孩子学习/小学数学_竞赛"

        # 8. 小学数学常规
        if any(k in c for k in ["口算", "计算", "期末", "试卷", "真题卷", "课本", "人教", "公文数学", "新加坡数学", "课课练", "核心概念", "洋葱数学", "火花", "数理化", "代数", "skill sharpeners", "摩比爱数学", "乐乐课堂小学数学", "数学城小兄妹", "万物有数学", "二升三", "二年级"]):
            return "01_孩子学习/小学数学_常规"

        # 9. 少儿通识与素养
        if any(k in c for k in ["scratch", "python", "编程", "机器人", "童行", "科学课", "雨果带你看世界", "神奇校车", "dk百科", "百科", "魔方", "ev3", "stem", "画啦啦", "画画", "绘画", "国画", "水彩画", "爱豆艺术", "毕加索", "美术", "粘土", "手工", "折纸", "书画", "电吹管", "围棋", "正面管教", "育儿", "发展表", "时间管理", "国家地理", "故宫", "上下五千年", "中国通史", "历史", "地理", "这是什么", "白泽", "恐龙星球", "数学的故事", "do you know", "whats.the.big.idea", "跳绳", "体能课", "迷宫", "七田真", "新年谜题", "寻宝", "初中", "中考", "小升初", "玩具", "万物运转", "山水", "三十六计", "春节习俗", "十二生肖", "draw", "cosplay"]):
            return "01_孩子学习/少儿通识与素养"

        return "06_其他待复核"

    def _check_deletion_candidate(self, u: AtomicUnit) -> bool:
        n = u.name.lower()
        protected = ["1-6", "1~6", "1～6", "1至6", "1到6", "一至六", "一到六", "全套", "全集", "大合集", "三年级", "3年级", "高思", "奥数", "大语文", "新概念", "牛津树", "raz", "海尼曼"]
        if any(k in n for k in protected):
            return False
        retire_kws = ["幼儿园", "小班", "中班", "大班", "幼小衔接", "巧虎", "乐高", "lego", "搭建图"]
        return any(k in n for k in retire_kws)

    def classify_all(
        self,
        unique_units: List[AtomicUnit],
        duplicates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """全流程执行 A+B 两级层级化分类，产出丰富、精准的移动规划记录"""
        self._log(f"开始对 {len(unique_units)} 个独立单元及 {len(duplicates)} 个查重副本执行两级层级化智能规划...")

        plan_records: List[Dict[str, Any]] = []
        rec_id = 1

        # 1. 录入查重副本 (隔离区)
        for dup in duplicates:
            target_path = f"/_待清理隔离区/01_确凿重复副本/{dup['duplicate_name']}"
            plan_records.append({
                "id": rec_id,
                "source_path": dup["duplicate_path"],
                "cleaned_name": dup["duplicate_name"],
                "action": "ISOLATE",
                "category": "_待清理隔离区/重复副本",
                "target_path": target_path,
                "reason": dup["reason"],
                "status": "CONFIRMED"
            })
            rec_id += 1

        # 2. 一级大领域分组
        domain_groups: Dict[str, List[AtomicUnit]] = defaultdict(list)
        for u in unique_units:
            dom = self.assign_domain(u)
            domain_groups[dom].append(u)

        # 3. 逐个大领域进行二级子系列细化
        for domain, units in domain_groups.items():
            if domain.startswith("_待清理隔离区"):
                for u in units:
                    target_path = f"/_待清理隔离区/02_超龄早教与乐高废弃/{u.name}"
                    plan_records.append({
                        "id": rec_id,
                        "source_path": u.path,
                        "cleaned_name": u.name,
                        "action": "ISOLATE",
                        "category": "_待清理隔离区/超龄早教与乐高废弃",
                        "target_path": target_path,
                        "reason": "经识别属于低幼早教、幼小衔接或弃用乐高积木图纸",
                        "status": "CONFIRMED"
                    })
                    rec_id += 1
                continue

            self._log(f"\n>>> 处理领域: 【{domain}】 (共 {len(units)} 项)")
            subtier_mapping = self._resolve_domain_subtiers(domain, units)

            for u in units:
                subtier = subtier_mapping.get(u.path, "综合系列")
                cleaned_name = self._sanitize_name(u.name)
                # 针对超短无意义名称（如 S05, 1-20, draw），从核心文件中提取更好读的名字
                cleaned_name = self._enrich_cryptic_name(u, cleaned_name)

                target_dir = f"/{domain}/{subtier}"
                target_path = f"{target_dir}/{cleaned_name}"

                plan_records.append({
                    "id": rec_id,
                    "source_path": u.path,
                    "cleaned_name": cleaned_name,
                    "action": "MOVE",
                    "category": f"{domain}/{subtier}",
                    "target_path": target_path,
                    "reason": f"A+B模型细化分类至 [{subtier}]",
                    "status": "CONFIRMED"
                })
                rec_id += 1

        self._log(f"\n全部规划记录已生成！共 {len(plan_records)} 项操作。")
        return plan_records

    def _resolve_domain_subtiers(self, domain: str, units: List[AtomicUnit]) -> Dict[str, str]:
        """针对一个大领域内的单元集合，协同调用 LLM (my-fast-gptoss) 与 Kev 完成二级子系列映射"""
        result_mapping: Dict[str, str] = {}
        pending_units: List[AtomicUnit] = []

        # 1. 获取当前大领域的标准候选子系列
        choices = STANDARD_SUBTIERS.get(domain, {})
        use_kev = self.kev.is_online() and len(choices) >= 2

        # 2. 检查断点续传缓存（严格校验缓存的子系列是否属于当前领域的候选集）
        for u in units:
            if u.path in self.cache:
                cached_sub = self.cache[u.path]
                if not choices or cached_sub in choices:
                    result_mapping[u.path] = cached_sub
                    continue
            pending_units.append(u)

        # 3. 构造给大模型的丰富上下文列表（包含名称、路径及文件样本）
        items_payload = []
        for u in pending_units:
            items_payload.append({
                "path": u.path,
                "folder_name": u.name,
                "context": self.get_unit_context(u)
            })

        # 采用切片批次（每批最多 30 项，保证上下文紧凑），请求大模型
        batch_size = 25
        for i in range(0, len(items_payload), batch_size):
            batch = items_payload[i:i + batch_size]
            prompt = (
                f"你是一名专业的文件体系整理专家。当前大类是【{domain}】。\n"
                f"参考推荐的二级子系列包括：{list(choices.keys()) if choices else '请自主归纳4~6个规范子系列'}\n\n"
                f"待分类资源列表（含目录名与文件样本，请重点通过文件样本识别真实内容）：\n"
                f"{json.dumps(batch, ensure_ascii=False, indent=2)}\n\n"
                "任务：\n"
                "1. 为列表中的每个目录指定最贴切的二级子系列名称（如：'高思数学系列'、'学而思奥数系列'、'AMC8竞赛系列'、'凯叔讲故事系列'等）；\n"
                "2. 严格输出 JSON 格式（键为原 path，值为子系列名称）：\n"
                "{\n"
                "  \"/原路径1\": \"子系列名称1\",\n"
                "  \"/原路径2\": \"子系列名称2\"\n"
                "}"
            )

            try:
                t0 = time.time()
                resp = self.client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a professional file hierarchy organizer. Output strictly valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    timeout=30.0
                )
                raw = resp.choices[0].message.content or ""
                parsed = parse_llm_json(raw)
                elapsed = time.time() - t0

                if isinstance(parsed, dict) and parsed:
                    self._log(f"  [LLM ({LLM_MODEL})] 批次 {i//batch_size+1} 成功处理 {len(parsed)} 项 (耗时: {elapsed:.2f}s)")
                    for k, v in parsed.items():
                        norm_k = "/" + k.strip().strip("/")
                        clean_v = str(v).strip(" /\\")
                        result_mapping[norm_k] = clean_v
                        result_mapping[k.strip()] = clean_v
                        self.cache[norm_k] = clean_v
                else:
                    raise ValueError("LLM 返回空字典或格式非法")

            except Exception as e:
                self._log(f"  [LLM 异常] {e}，启用 Kev/规则兜底机制...")
                # 回退：如果有 Kev 在线且有候选标准，调用本地 Kev 逐项判定
                for item in batch:
                    p = item["path"]
                    ctx = item["context"]
                    assigned_subtier = None
                    if use_kev:
                        try:
                            assigned_subtier = self.kev.classify_choice(ctx, choices, timeout=1.5)
                        except Exception:
                            assigned_subtier = None
                    if not assigned_subtier:
                        # 启发式规则兜底
                        assigned_subtier = self._fallback_subtier(domain, item["folder_name"], ctx)

                    result_mapping[p] = assigned_subtier
                    self.cache[p] = assigned_subtier

            # 每批次处理完即时落盘断点
            self._save_cache()

        # 检查是否有漏掉的项
        for u in pending_units:
            if u.path not in result_mapping:
                def_sub = list(choices.keys())[0] if choices else "综合系列"
                result_mapping[u.path] = def_sub
                self.cache[u.path] = def_sub

        self._save_cache()
        return result_mapping

    def _fallback_subtier(self, domain: str, name: str, context: str) -> str:
        """纯本地启发式二级子目录兜底"""
        c = f"{name} {context}".lower()
        if "高思" in c: return "高思数学系列"
        if "学而思" in c or "大白本" in c or "大白皮" in c or "xesx" in c: return "学而思奥数系列"
        if "amc" in c: return "AMC8竞赛系列"
        if "袋鼠" in c: return "袋鼠数学系列"
        if any(k in c for k in ["数学大王", "希望", "华杯", "走美", "迎春", "sasmo", "早培", "七大能力", "思维", "数独"]): return "综合杯赛与思维"
        if "公文" in c or "新加坡" in c: return "公文与新加坡数学"
        if "口算" in c or "计算" in c: return "计算与口算速算"
        if any(k in c for k in ["课本", "人教", "北师大", "苏教", "课课练", "乐乐课堂"]): return "教材课本与同步练"
        if any(k in c for k in ["期末", "真题", "测试", "模考"]): return "期末真题与综合测试"
        if "思泉" in c: return "思泉大语文系列"
        if "窦神" in c or "诸葛" in c: return "窦神与诸葛学堂"
        if any(k in c for k in ["好字在", "字成方圆", "字帖", "控笔", "识字", "书法", "描红"]): return "汉字识字与字帖控笔"
        if any(k in c for k in ["古诗", "文言文", "三字经", "弟子规", "声律启蒙", "中国通史", "历史"]): return "国学古诗与文言文"
        if any(k in c for k in ["作文", "阅读", "写话", "一亩中文"]): return "阅读与作文写作营"
        if "牛津树" in c: return "牛津树分级系列"
        if "raz" in c: return "Raz原版分级阅读"
        if "新概念" in c or "grammar" in c or "语法" in c: return "新概念与语法体系"
        if "ket" in c or "pet" in c or "剑桥" in c: return "剑桥考级与真题"
        if any(k in c for k in ["绘本", "sherlock", "dragon master", "little bear", "bear", "can read", "gorilla", "caterpillar", "bus", "journey to the west"]): return "原版绘本与名著名篇"
        if "凯叔" in c: return "凯叔讲故事系列"
        if "钱儿爸" in c or "钱爸" in c: return "钱儿爸系列"
        if "评书" in c or "单田芳" in c: return "传统评书与名著曲艺"
        if any(k in c for k in ["小鼠波波", "佩奇", "maisy", "raa raa", "blippi", "动画"]): return "英文原版动画与听读"
        if any(k in c for k in ["成语", "童话", "故事"]): return "成语故事与睡前童话"
        if any(k in c for k in ["alpha", "gtja", "因子"]): return "量化与金工多因子"
        if any(k in c for k in ["期权", "择时", "基差", "hft", "套利", "高频"]): return "期权基差与高频策略"
        if any(k in c for k in ["研报", "证券", "调研", "fintech", "银行", "机构配置", "宏观"]): return "券商研报与宏观策略"
        if any(k in c for k in ["论文", "机器学习", "学术", "apama"]): return "学术文献与机器学习"
        if any(k in c for k in ["罗辑思维", "德鲁克", "从众危机", "魔鬼经济学", "经管", "第", "期"]): return "经管人文与社科精读"
        if any(k in c for k in ["编程", "python", "scratch", "ev3", "机器人"]): return "少儿编程与机器人"
        if any(k in c for k in ["百科", "神奇校车", "魔方", "科学", "这是什么", "白泽", "恐龙"]): return "科普百科与智力启蒙"
        if any(k in c for k in ["美术", "画画", "绘画", "国画", "水彩", "毕加索", "书法", "粘土", "手工", "折纸", "draw", "山水"]): return "少儿美育与书法"
        if any(k in c for k in ["历史", "地理", "故宫", "通史", "三十六计"]): return "少儿历史与地理"
        if any(k in c for k in ["正面管教", "育儿", "家长", "时间管理", "英语学霸"]): return "家庭教育与育儿方法"
        if any(k in c for k in ["跳绳", "体能"]): return "少儿体育与体能健康"
        if any(k in c for k in ["相册", "照片", "dcim", "来自：", "来自:", "写真", "修图", "photo"]): return "家庭相册与相机拍摄"
        if any(k in c for k in ["软件", "total", "win11", "apk", "tools", "nds", "gba", "mod", "iso"]): return "系统软件与装机工具"
        return "综合系列"

    def _sanitize_name(self, name: str) -> str:
        """剥离公众号水印与多余宣传字符"""
        cleaned = name
        patterns = [
            r"【.*?(?:公众号|微信|分享|首发|无密|整理|独家|精选|免费|推荐).*?】",
            r"\[.*?(?:公众号|微信|分享|首发|无密|整理|独家|精选|免费|推荐).*?\]",
            r"（.*?(?:公众号|微信|分享|首发|无密|整理|独家|精选|免费|推荐).*?）",
            r"^\d+[\.、\-\_\s]+",
            r"[\-_]?(?:高清|1080p|720p|4k|超清|无水印|完整版|未删减)$",
            r"【完结】", r"【全套】", r"【最新】"
        ]
        for p in patterns:
            cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip(" -_[]【】()（）")
        return cleaned or name

    def _enrich_cryptic_name(self, u: AtomicUnit, current_name: str) -> str:
        """通过文件或子目录样本润色超短/无意义名称（如 45, 13vol, S05, 1-20, draw, 第10期）"""
        clean = current_name.strip()
        p = u.path.lower()
        sf = " ".join(u.sample_files).lower()
        sd = " ".join(u.sample_subdirs).lower()
        c = f"{clean} {p} {sf} {sd}"

        # 1. 我的文档中关于社科经管读书会的 [第X期]
        if "/我的文档" in p and ("第" in clean or re.match(r"^\[?第?\d+期?\]?$", clean)):
            return f"罗辑思维精选读书会_{clean}"

        # 2. 若名称极短或纯数字/季集/纯英文简称
        if len(clean) <= 6 or re.match(r"^[\d\s\-_.]+$", clean) or clean.lower() in ["draw", "contacts", "sanxi", "xesx", "photo", "cd", "nds", "ss", "wan", "hft", "真题", "早培", "山水", "更新完"]:
            if "the.boys" in sf or "the boys" in sf: return f"黑袍纠察队_{clean}"
            if "陈情令" in sf: return "陈情令影视原声与特辑" if "音乐" in sf or "专辑" in sf else "陈情令特别剪辑版"
            if "san11" in sf: return "三国志11游戏MOD整合包"
            if "刘宝平" in sf or clean.lower() == "draw": return "刘宝平少儿绘画国画教程"
            if "contacts" in clean.lower(): return "手机通讯录备份"
            if "photo" in clean.lower(): return "家庭相册照片备份"
            if "cd" == clean.lower(): return "经典CD音频刻录镜像"
            if "zm模考" in sf or "ihc" in sf: return f"走美杯模考真题_{clean}"
            if "sasmo" in sf or "mathmaster" in sf: return "SASMO新加坡数学竞赛真题"
            if "门萨" in sf or "早培" in sf: return "人大附中早培班真题与门萨思维"
            if "习题课" in sd and "xesx" in clean.lower(): return "学而思创新X班习题讲义"
            if "ftr_tick" in sf or "hft" in clean.lower(): return "高频交易Tick数据与策略"
            if "good.wife" in sf or "goodwife" in clean.lower(): return "傲骨贤妻 The Good Wife"
            if "tt9419884" in sf or "博士2" in clean: return "奇异博士2高清电影"
            if "北欧女神" in sf or "nds" in clean.lower(): return "NDS掌机经典游戏中文合集"
            if "ssandroid" in sf or "ss" == clean.lower(): return "Shadowsocks网络工具配置"
            if "proposta di matrimonio" in sf: return "婚礼策划与求婚影片素材"
            if "山水" in clean: return "山水国画少儿艺术课"
            if "主教材g1" in sd or "g1" == clean: return "美加分级阅读教材G1"

        return current_name
