# -*- coding: utf-8 -*-
"""标签智能整理：收集 → 规则检测 → AI 分类（DashScope 通义千问）→ 执行。

问题背景（来自真实库诊断）：
- 演员名 / 片商 / 发行 / 系列 / 导演被刮削源错误写进 tag（如「片商: MOODYZ」「由良かな」）
- 同一概念多种写法（简繁、中英、平台别称）导致无法检索
- 大量低频碎片 tag

流程：
1. collect_tag_stats(folders)：扫描统计 tag/genre，收集演员/片商等结构化信息
2. rule_detect(stats)：纯规则标记错误标签（免费、确定性强）
3. ai_classify(...)：调用通义千问对剩余 tag 做语义分类（keep/remove/merge + 规范名）
4. apply_organize(...)：按映射删除/替换 NFO 的 tag/genre 元素并保存
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from core.nfo import NfoFile
from core.scanner import find_nfo_files
from core.tag_analyzer import CENSORED_KEYWORDS, UNCENSORED_KEYWORDS

# ---------- 数据模型 ----------

PREFIX_NOISE = ("片商:", "发行:", "系列:", "导演:", "编号:",
                "片商：", "发行：", "系列：", "导演：", "编号：",
                "studio:", "distributor:", "series:", "director:")

# 显然不是内容标签的噪声模式
NOISE_RE = [
    re.compile(r"^[\d]+$"),                       # 纯数字
    re.compile(r"^[A-Za-z0-9]+$", re.I),          # 纯字母数字（编号类）
    re.compile(r"^\d+P$", re.I),                  # 480P 等（会误删 1080P 吗？保留黑名单外）
]


@dataclass
class TagStat:
    name: str
    count: int = 0
    in_tag: bool = False          # 出现在 <tag>
    in_genre: bool = False        # 出现在 <genre>
    samples: list = field(default_factory=list)
    rule_flag: str = ""           # 规则检测结果："" / "演员名" / "片商" / "前缀" / "噪声"
    ai_action: str = ""           # AI：keep / remove / merge
    ai_target: str = ""           # merge 的规范名
    ai_reason: str = ""
    sim_target: str = ""          # 规则层同义检测建议的合并目标（保守，仅同义/近义）
    sim_flag: str = ""            # sim 类型："" / "近义" / "截断"（截断=同内容被截取成不同长度写法）
    user_action: str = ""         # 用户覆盖：keep / remove / merge
    user_target: str = ""


@dataclass
class TagStats:
    tags: dict = field(default_factory=dict)      # name -> TagStat
    actors: set = field(default_factory=set)
    studios: set = field(default_factory=set)
    nfo_files: list = field(default_factory=list)  # 所有 NFO 路径
    folders: list = field(default_factory=list)


# ---------- 1. 收集 ----------

def collect_tag_stats(folders: list[str], on_progress=None,
                      cancel_check=None) -> TagStats:
    """扫描目录收集 tag/genre 统计。

    on_progress(n)：每处理一个 NFO 回调（用于进度，n=已处理数）。
    cancel_check()：返回 True 时提前结束（返回已统计部分），用于后台线程取消。
    两者默认 None，保持与原调用方（脚本/测试）兼容。
    """
    stats = TagStats(folders=list(folders))
    # 先一次性拿到全部 NFO 路径（仅遍历目录、不解析，开销很小），用于进度总数
    all_files = []
    for d in folders:
        if not os.path.isdir(d):
            continue
        all_files.extend(find_nfo_files(d, recursive=True))
    total = len(all_files) or 1
    for idx, p in enumerate(all_files):
        if cancel_check and cancel_check():
            break
        stats.nfo_files.append(p)
        nfo = NfoFile(p)
        if not nfo.is_valid:
            continue
        for a in nfo.actors:
            stats.actors.add(a)
        for s in nfo.get_texts("studio"):
            stats.studios.add(s)
        for dr in nfo.get_texts("director"):
            stats.studios.add(dr)
        seen = set()
        for t in nfo.tags:
            if t in seen:
                continue
            seen.add(t)
            st = stats.tags.setdefault(t, TagStat(name=t))
            st.count += 1
            st.in_tag = True
            if len(st.samples) < 3:
                st.samples.append(p)
        seen = set()
        for g in nfo.genres:
            if g in seen:
                continue
            seen.add(g)
            st = stats.tags.setdefault(g, TagStat(name=g))
            st.count += 1
            st.in_genre = True
            if len(st.samples) < 3:
                st.samples.append(p)
        if on_progress and (idx % 200 == 0 or idx == total - 1):
            on_progress(idx + 1, total)
    if on_progress:
        on_progress(total, total)
    return stats


# ---------- 2. 规则检测 ----------

def rule_detect(stats: TagStats) -> None:
    for st in stats.tags.values():
        name = st.name.strip()
        low = name.lower()
        # 结构化前缀（片商:/发行:/系列:/导演:）
        hit = False
        for pref in PREFIX_NOISE:
            if low.startswith(pref.lower()):
                st.rule_flag = "前缀"
                hit = True
                break
        if hit:
            continue
        # 与演员同名
        if name in stats.actors:
            st.rule_flag = "演员名"
            continue
        # 与片商/导演同名
        if name in stats.studios:
            st.rule_flag = "片商"
            continue
        # 纯噪声（数字/编号类）——高频 tag 不算噪声（如 FC2 平台标签，交给 AI 判断）
        if any(rx.match(name) for rx in NOISE_RE) \
                and not re.search(r"(1080|720|2160|4k|h264|h265|hevc|sdr|hdr|fps)", low) \
                and st.count <= 8:
            st.rule_flag = "噪声"
            continue
        # 高频"疑似内容"但含明显广告/刮削痕迹
        if re.search(r"(無碼|無码|无码|破解|流出|uncensored|decensored)", low, re.I) \
                and st.count <= 2:
            pass  # 留给 AI 判断


# ---------- 2.5 同义/近义标签检测（规则层，日文友好） ----------

def _script_rank(name: str) -> int:
    """书写体系优先级（值小优先）：纯简体汉字 > 汉字(含繁体/日文变体) > 汉字+假名 > 纯假名 > 其他。

    用于「简体优先」合并方向：同义写法 count 相近时，保留简体中文写法而不是繁体/日文。
    """
    has_kana = any("\u3040" <= ch <= "\u30ff" for ch in name)
    han = sum(1 for ch in name if "\u4e00" <= ch <= "\u9fff")
    non_simp = sum(1 for ch in name if ch in _KANJI_VAR)  # 繁体/日文汉字（已映射到简体的 key）
    if han and not non_simp and not has_kana:
        return 0
    if han and not has_kana:
        return 1
    if han and has_kana:
        return 2
    if has_kana:
        return 3
    return 4


def _best_merge_target(stats: TagStats, names: set | list) -> str:
    """在候选写法里选「合并目标」（统一方向规则，规则层与 AI 层共用）：

    1. 少往多合：出现次数明显多的写法优先保留（主流写法）；
    2. 简体优先：与第一名次数接近（差 ≤3 次 或 ≤15%）且书写更简体（简体中文 > 繁体/日文
       汉字 > 汉字+假名 > 纯假名）时，简体写法胜出——避免同义词 count 接近时选到日文/繁体；
    3. 字典序稳定兜底。
    """
    def _cnt(n: str) -> int:
        st = stats.tags.get(n)
        return st.count if st else 0

    items = sorted(names, key=lambda n: (-_cnt(n), _script_rank(n), n))
    best, best_c = items[0], _cnt(items[0])
    for n in items[1:]:
        if _script_rank(n) >= _script_rank(best):
            continue  # 不比 best 更简体，不参与竞争
        c = _cnt(n)
        if best_c - c <= 3 or (best_c > 0 and (best_c - c) / best_c <= 0.15):
            return n
    return best


def _resolve_ai_target(stats: TagStats, src: str, ai_target: str) -> str:
    """AI 给出 merge 目标后做方向校正：目标写法向「主流 + 简体」收敛。

    AI 只知道语义，不知道库内各写法的次数/简繁分布，经常给出非主流或日文写法
    （如 贫乳/平胸 选了次数少的贫乳、顔射/颜射 选了日文顔射）。
    校正：pool = {src, ai_target} ∪ 所有与两者归一化同键的写法，再走 _best_merge_target。
    语义同义但键不同（贫乳/平胸）也能落到次数多、更简体的写法。
    """
    pool = {src, ai_target}
    t_key, s_key = norm_key(ai_target), norm_key(src)
    for n in stats.tags:
        nk = norm_key(n)
        if nk and (nk == t_key or nk == s_key):
            pool.add(n)
    return _best_merge_target(stats, pool)


# 片假名 → 平假名（同一词汇不同书写形式视为同词，如 ハメ撮り=はめ撮り）
_KATA2HIRA = {chr(cp): chr(cp - 0x60) for cp in range(0x30A1, 0x30F7)}

# 常见日文汉字 ↔ 简体中文变体（标签常用字，映射后 顔射=颜射、無碼=无码）
_KANJI_VAR = {
    "顔": "颜", "體": "体", "髮": "发", "髪": "发", "關": "关", "學": "学",
    "壓": "压", "圖": "图", "畫": "画", "與": "与", "說": "说", "會": "会",
    "覺": "觉", "讓": "让", "發": "发", "點": "点", "萬": "万", "邊": "边",
    "無": "无", "雙": "双", "將": "将", "國": "国", "間": "间", "開": "开",
    "內": "内", "兩": "两", "個": "个", "寫": "写", "實": "实", "隱": "隐",
    "視": "视", "聽": "听", "聲": "声", "審": "审", "讀": "读", "話": "话",
    "語": "语", "對": "对", "見": "见", "愛": "爱", "時": "时", "産": "产",
    "場": "场", "広": "广", "発": "发", "訳": "译", "変": "变", "優": "优",
    "歴": "历", "歳": "岁", "専": "专", "従": "从", "気": "气", "収": "收",
    "運": "运", "選": "选", "連": "连", "達": "达", "東": "东", "児": "儿",
    "組": "组", "総": "总", "絶": "绝", "続": "续", "縄": "绳", "縁": "缘",
    "級": "级", "類": "类", "種": "种", "編": "编", "韓": "韩", "関": "关",
    "碼": "码", "線": "线", "絕": "绝", "澤": "泽", "樣": "样", "機": "机",
    "網": "网", "緒": "绪", "監": "监", "遺": "遗", "覽": "览",
    "複": "复", "雜": "杂", "異": "异", "團": "团", "應": "应", "當": "当",
    "戰": "战", "職": "职", "靈": "灵", "鄉": "乡", "農": "农", "塊": "块",
    "價": "价", "購": "购", "販": "贩", "題": "题", "願": "愿", "額": "额",
    "館": "馆", "飯": "饭", "飲": "饮", "飾": "饰", "馬": "马", "魚": "鱼",
    "質": "质", "術": "术", "島": "岛", "構": "构", "撥": "拨", "檔": "档",
    "證": "证", "証": "证", "準": "准", "製": "制", "衛": "卫", "廁": "厕", "繫": "系",
    "補": "补", "許": "许", "幫": "帮", "厭": "厌", "乗": "乘", "拠": "据",
    "騎": "骑", "獸": "兽", "龜": "龟", "嬢": "娘", "捜": "搜", "懐": "怀",
    "療": "疗", "劇": "剧", "薬": "药", "価": "价", "側": "侧", "検": "检",
    "挙": "举", "焼": "烧", "純": "纯", "訪": "访", "負": "负", "違": "违",
    "過": "过", "還": "还", "銭": "钱", "閉": "闭", "際": "际", "隣": "邻",
    "険": "险", "離": "离", "顎": "颚", "髄": "髓", "懸": "悬", "纏": "缠",
    "触": "触", "譲": "让", "護": "护", "醜": "丑", "醤": "酱", "釈": "释",
    "鉢": "钵", "鐘": "钟", "陥": "陷", "給": "给", "維": "维", "繊": "纤",
    "縮": "缩", "脅": "胁", "膣": "膣", "艶": "艳", "芸": "艺",
    "蘇": "苏", "藪": "薮",
}

# 用于归一化的标点/空白（含日文长音符 ー：ロングヘア=ロングヘアー）
_PUNCT_RE = re.compile(
    r"[\s\u3000·・,，.。、！!？?：:；;｜|／/\\\-–—_~～*＊\"'「」『』（）()\[\]【】<>〈〉ー～]+")
# 允许合并的送假名白名单（去掉后语义不变；ら/れ/られ 等被动·否定形会改变词义，不合并）
_OKURIGANA = {"し", "き", "り", "み", "い", "う", "ち", "ぎ", "び", "ひ", "ふ", "え"}
# 允许合并的装饰性后缀（美少女系=美少女、アイドル級=アイドル；ロング/服/嬢/人 等有实义，不合并）
_DECOR_SUFFIX = {"系", "級", "级", "編", "编", "類", "类", "種", "种", "色", "向け", "版", "篇"}
# 截断规则中禁止合并的假名功能词后缀（被动/否定/敬语等，会改变词义）
_KANA_FUNCTION_SUFFIX = {"られ", "れて", "れ", "ない", "なし", "ます", "ませ",
                         "ません", "ちゃ", "しま", "たい", "れる", "させ",
                         "らせ", "くて", "ながら"}

# 独立实义尾巴（真实内容词）：与「限定前缀」组合后表达更具体的复合概念，且语义**不等于**前缀。
# 典型：无码破解≠无码（破解是编辑行为，无码是打码状态）、无码流出≠无码（流出是来源/渠道）、
# 高画质特典≠高画质（特典是附赠内容）、无码完結≠无码、无码素人≠素人。
# 命中此集合的尾巴禁止被截断规则吃进前缀主体（守卫 A：完整复合词保留、前缀不反向吞）。

# 属性/限定维度词：修饰「打码状态 / 画质 / 完整度 / 来源 / 形态 / 合集性质」等维度，
# 不表达内容主体。当「维度词 + 内容实义词」（无码+破解、高画质+特典）存在时，
# 说明该复合词表达的是**更具体、语义≠维度词本身**的概念，应独立保留，且
# 独立的内容实义词（破解/特典）应从属回完整的复合词（破解→无码破解）。
#
# 这条既是「守卫A」的判定基准，也是「守卫B」后缀吸收的启用开关。
# 不再只靠穷举 _MODIFIER_PREFIX_RE（20 个）——任何表达这些维度的词都算，
# 故允许在测试中传入自定义维度词表做闭环验证。
_MODIFIER_PREFIX_RE = re.compile(
    r"^(无码|有码|無碼|无修|無修|骑兵|步兵|马赛克|打码|高清|高画质|高画質|"
    r"4k|4K|超清|完整|原版|极致|无损|高品質|限定|收藏|典藏|合集|完整版|"
    r"無修正|无修正|無碼|有碼|オリジナル|抱き枕|同人|合集|精選|精选|数字版|"
    r"特典|復刻|复刻|加长|完整版|导演剪辑|演员版|字幕版|无字幕|無字幕|"
    r"合成|步兵|骑兵)", re.I)

# 实义词尾（守卫B 吸收的兜底白名单）。吸收方向由结构规则判定，本表仅作为高频确认。
_ABSORB_TAIL = {"破解", "流出", "特典", "完結", "生肉", "原盘", "本子", "写真", "资源", "种子"}

# 介质/类型后缀：与「内容词」组合成「内容 + 介质」型复合词（美少女電影=美少女+電影、
# 素人作品=素人+作品、绝顶高潮=绝顶+高潮）。这类复合词的**前半内容词**是独立内容概念，
# 语义 ≠ 复合词（人物 美少女 ≠ 影片类型 美少女電影），因此不能把内容词并入复合词，
# 反而是复合词碎片应并入内容词（美少女電影→美少女、素人作品→素人）。
_MEDIA_TAIL = {"電影", "电影", "影片", "作品", "動画", "动画", "アニメ", "ドラマ",
               "系列", "シリーズ", "劇場版", "篇", "特集", "合集", "集"}

# 语义变化型后缀：接在内容词后，使复合词语义**比内容词更具体/抽象**（背徳感=背德+感、
# メンエス嬢=男公关+女、ボーイッシュ中身×女）。与「内容+介质」类似——内容是主体，
# 后缀是修饰/定性。因此方向应向**内容主体**收敛（背徳感→背徳、メンエス嬢→メンエス），
# 而不是反向把内容词并进派生词。尤其当内容主体 count 更高（更主流）时更不能反向。
_SEM_CHANGE_SUFFIX = {"感", "性", "気", "气", "味", "嬢", "女", "化", "者", "系",
                      "向け", "中身", "体", "型", "类", "類"}


def _classify_compound(name: str):
    """识别复合词结构，用于统一合并方向。

    返回 (kind, a, b)：
    - kind = "attr_content"：属性/限定词 a + 内容 b（无码破解、高画质特典）
    - kind = "content_media"：内容 a + 介质/类型词 b（美少女電影、素人作品）
    - kind = "content_sem"：内容 a + 语义变化后缀 b（背徳感、メンエス嬢）
    - kind = ""：非上述结构（普通词）
    """
    # 1) 属性 + 内容：先用已扩展的 _MODIFIER_PREFIX_RE 命中最长属性前缀
    m = _MODIFIER_PREFIX_RE.match(name)
    if m and len(name) > len(m.group(0)):
        rest = name[len(m.group(0)):]
        # 剩余部分存在汉字/假名实词，且不是纯数字/功能词 → 视为实义内容
        if re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", rest) and not re.fullmatch(r"\d+", rest):
            return "attr_content", m.group(0), rest
    # 2) 内容 + 介质：后缀命中介质表
    for t in _MEDIA_TAIL:
        if name.endswith(t) and len(name) > len(t):
            return "content_media", name[:-len(t)], t
    # 3) 内容 + 语义变化后缀：后缀命中单字语义变化表（背徳感、メンエス嬢）
    for t in _SEM_CHANGE_SUFFIX:
        if name.endswith(t) and len(name) > len(t):
            return "content_sem", name[:-len(t)], t
    return "", "", ""


# 属性/限定维度词：修饰「打码状态 / 画质 / 完整度 / 来源 / 形态 / 合集性质」等维度，
# 不表达内容主体。当「维度词 + 内容实义词」（无码+破解、高画质+特典）存在时，
# 说明该复合词表达的是**更具体、语义≠维度词本身**的概念，应独立保留，且
# 独立的内容实义词（破解/特典）应从属回完整的复合词（破解→无码破解）。
#
# 这条既是「守卫A」的判定基准，也是「守卫B」后缀吸收的启用开关。
# 不再只靠穷举 _MODIFIER_PREFIX_RE（20 个）——任何表达这些维度的词都算，
# 故允许在测试中传入自定义维度词表做闭环验证。
_MODIFIER_PREFIX_RE = re.compile(
    r"^(无码|有码|無碼|无修|無修|骑兵|步兵|马赛克|打码|高清|高画质|高画質|"
    r"4k|4K|超清|完整|原版|极致|无损|高品質|限定|收藏|典藏|合集|完整版|"
    r"無修正|无修正|無碼|有碼|オリジナル|抱き枕|同人|合集|精選|精选|数字版|"
    r"特典|復刻|复刻|加长|完整版|导演剪辑|演员版|字幕版|无字幕|無字幕|"
    r"合成|步兵|骑兵)", re.I)





def norm_key(name: str) -> str:
    """归一化标签名：小写 → 全角转半角 → 片假名转平假名 → 去标点空白 → 汉字变体统一。
    用于同义词匹配（顔射/颜射、無碼/无码、ハメ撮り/はめ撮り、中出し/中出…）。
    """
    s = unicodedata.normalize("NFKC", name).lower()
    s = "".join(_KATA2HIRA.get(c, c) for c in s)
    s = _PUNCT_RE.sub("", s)
    s = "".join(_KANJI_VAR.get(c, c) for c in s)
    return s


def suggest_similar_merges(stats: TagStats, ratio_thr: float = 0.85) -> int:
    """规则层检测同义/截断标签，写入 st.sim_target + st.sim_flag。

    - sim_flag="近义"：同一概念的不同写法（保守合并）
      1. 归一化后完全相同：顔射/颜射、無碼/无码、ハメ撮り/はめ撮り
      2. 仅差白名单送假名（多出的假名在末尾，且去掉后语义不变）：中出し→中出、潮吹き→潮吹
         （被动/否定形如 寝取られ/寝取り 之类的长假名后缀不合并）
      3. 仅差装饰性后缀（系/級/编/类/种/色 等）：美少女系→美少女、アイドル級→アイドル
    - sim_flag="截断"：同一内容被刮削源截取成不同长度（前缀包含关系，涉及碎片标签）
      4. 短词是长词的前缀（多出 ≤3 字且不是纯平假名助动词）：黑髪ロング→黒髪、
         複数プレイ→複数、素人娘→素人、妊娠マンコ→妊娠、絶叫アクメ→絶叫
         （避免 美少女→少女 这类词尾包含；避免 ロリ→ロリコン 过长后缀）
    返回发现的疑似同义/截断组数。
    """
    cand = [st for st in stats.tags.values() if not st.rule_flag]
    by_key: dict[str, list[str]] = defaultdict(list)
    for st in cand:
        by_key[norm_key(st.name)].append(st.name)

    def pick(names: list[str]) -> str:
        # 规范名 = 出现次数明显多 + 简体优先 + 字典序（统一方向规则）
        return _best_merge_target(stats, names)

    def pick_orig_priority(orig_names: list[str]) -> str:
        # 从「同一归一化键的一组原始名」里选最主流的一个（次数多 + 简体优先），
        # 用于把「方向目标」落在真实 tag 名上（避免直接拿归一化键 target 导致落空）。
        return _best_merge_target(stats, orig_names)

    def set_target(names: list[str], target: str, flag: str):
        for n in names:
            if n != target:
                st = stats.tags[n]
                st.sim_target = target
                st.sim_flag = flag

    groups: list[dict] = []
    # 1) 归一化完全相同 → 近义
    for key, names in by_key.items():
        if len(names) >= 2:
            groups.append({"names": set(names), "flag": "近义"})
    # 2) 仅差 1 个白名单送假名（多出的假名必须在**末尾**）→ 近义
    keys = list(by_key.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            if (len(a) == len(b) + 1 and a[:-1] == b and a[-1] in _OKURIGANA) or \
               (len(b) == len(a) + 1 and b[:-1] == a and b[-1] in _OKURIGANA):
                names = by_key[a] + by_key[b]
                groups.append({"names": set(names), "flag": "近义"})
    # 3) 仅差装饰性后缀（≤2 字且在白名单内）→ 近义
    key_sorted = sorted(by_key.keys(), key=len, reverse=True)
    for a in key_sorted:
        for b in key_sorted:
            if a == b or len(b) < 3:
                continue
            extra = a[len(b):]
            if 0 < len(extra) <= 2 and a.startswith(b) and extra in _DECOR_SUFFIX:
                ratio = 2 * len(b) / (len(a) + len(b))
                if ratio >= ratio_thr:
                    names = by_key[a] + by_key[b]
                    groups.append({"names": set(names), "flag": "近义"})
    # 4) 截断：短词是长词的前缀 → 合并到出现次数更多的主流写法
    #    护栏：多出部分 ≤3 字、不含数字/字母（否则 3P、4P→3P、アナル貫通ATM 会被误并）；
    #    多出部分不是功能词假名后缀（られ/れて/ない 等会改变词义，如 寝取られ≠寝取）；
    #    短词不可是纯数字（18歳→18 禁止）
    #
    #    【通用化守卫】（本轮：不再只靠枚举 _MODIFIER_PREFIX_RE/_ABSORB_TAIL 的白名单，
    #     而是用「属性维度词 + 内容实义词」的结构规则识别任意同类组合）：
    #    守卫A：长词 a 若是「属性/限定词(无码/高画质/4K/合集…) + 内容实义词(破解/流出/特典/素人…)」
    #           的复合概念（如 无码破解、无码流出、高画质特典、无码素人），语义 ≠ 属性词 b
    #           （无码）本身，则禁止 a → b 反向合并，让复合词独立保留，避免被吞进「无码」。
    #           例外：素人娘→素人、黒髪ロング→黒髪 这类尾巴**不是**「属性+内容」复合概念，
    #           不受影响，照常合并。
    #    守卫B：库里若存在「属性词 + 内容实义词」的完整复合词 a（无码破解），且这个
    #           内容实义短词 b（破解）单独存在，则 b 应从属于 a（破解→无码破解），
    #           而不是被判删除或流落到其他 tag。（方向固定 b→a，复合词才是规范表达）

    # 先收集「完整复合词对」：a = 属性前缀(命中的限定词) + 内容实义词
    # → absorb_map: 内容实义词 b -> 完整复合词 a（破解 -> 无码破解）
    absorb_map: dict[str, str] = {}        # 内容实义尾词 -> 完整复合词
    for a in key_sorted:
        m = _MODIFIER_PREFIX_RE.match(a)
        if not m:
            continue
        prefix = m.group(0)
        content = a[len(prefix):]
        if not content:
            continue
        # 内容部分必须是「实义内容」——只要是中文/汉字/假名实词（非纯数字/非功能词后缀），
        # 就能参与吸收（不再局限于 _ABSORB_TAIL 白名单）。
        # 但保留 _ABSORB_TAIL 作为**最保守、最确定**的实义词（破解/流出/特典/本子/写真…），
        # 对未收录的实义词也开放（如 素人/完結/原盘/生肉…），只是标记为更宽松的「吸收候选」。
        if re.search(r"[0-9a-z]", content, re.I):
            continue
        if content in _KANA_FUNCTION_SUFFIX:
            continue
        # 记录：同一内容词若有多个复合写法（无码破解/有码破解），取出现次数多者为主
        cur = absorb_map.get(content)
        if cur is None or (stats.tags.get(a).count if a in stats.tags else 0) > \
                (stats.tags.get(cur).count if cur in stats.tags else 0):
            absorb_map[content] = a

    for a in key_sorted:
        for b in key_sorted:
            if a == b or len(b) < 2 or len(a) <= len(b):
                continue
            if not a.startswith(b):
                continue
            extra = a[len(b):]
            if not (1 <= len(extra) <= 3):
                continue
            if re.search(r"[0-9a-z]", extra, re.I):
                continue
            # 守卫A：长词 a 若以「属性/限定词」开头 → a 是「属性 + 内容」复合概念
            #（无码破解/无码流出/高画质特典），语义 ≠ 前缀 b。禁止并入 b。
            # 例外：素人娘/黒髪ロング/妊娠マンコ 等不以属性词开头，照常合并。
            if _MODIFIER_PREFIX_RE.match(a):
                continue
            if extra in _KANA_FUNCTION_SUFFIX:
                continue  # 功能词假名后缀（被动/否定/敬语）跳过
            if re.fullmatch(r"\d+", b):
                continue  # 短词是纯数字（如 18）不合并
            # 守卫C：「内容 + 介质 / 语义变化后缀」型长词 a = 内容主体 b + 后缀
            #（美少女電影=美少女+電影、素人作品=素人+作品、背徳感=背徳+感、
            #  メンエス嬢=メンエス+嬢）——a 是 b 的细分/派生，语义 ≤ b 或 ≠ b（人物≠影片类型）。
            # 不能按次数把 b 并进 a；方向应为 a → b（细分碎片并入内容主体）。
            # 例：美少女電影→美少女、素人作品→素人、背徳感→背徳（若 b 更主流）。
            # 注意：_classify_compound 须用**原始 tag 名**（by_key[a] 的原始写法），
            # 因为归一化键会把 嬢→娘、質→质 等字形改动，导致后缀识别失效。
            _a_orig = pick_orig_priority(by_key[a])
            _c_kind, _c_head, _c_tail = _classify_compound(_a_orig)
            if _c_kind in ("content_media", "content_sem"):
                names = by_key[a] + by_key[b]
                # 显式标记方向：内容主体 b 为规范目标（取 b 组最主流原始名，避免归一化键漂移）
                _bt = pick_orig_priority(by_key[b])
                groups.append({"names": set(names), "flag": "截断", "_target": _bt})
                continue
            names = by_key[a] + by_key[b]
            groups.append({"names": set(names), "flag": "截断"})
    # 后缀吸收（守卫B）：独立内容实义词 b（破解/流出/特典/素人/完結/生肉…）若库里有
    #「属性词 + b」的完整复合词 a（无码破解/高画质特典/无码素人…），则把 b 并入 a。
    # 方向固定为 b → a（独立短词并入完整复合词），不交给次数/简繁——因为复合词才是规范、
    # 更完整的表达（破解 归入 无码破解），否则会反向把复合词降级成短词或判删除。
    absorbed: list[dict] = []
    for b, a in absorb_map.items():
        if b == a or b not in by_key or a not in by_key:
            continue
        names = list(by_key[b]) + list(by_key[a])
        # 吸收方向固定 b→a：取复合词 a 组最主流原始名（次数多 + 简体优先）
        _at = pick_orig_priority(by_key[a])
        absorbed.append({"names": set(names), "flag": "截断", "_target": _at})
    groups.extend(absorbed)
    # 去重合并同一组（可能被多条规则命中），并统一目标与标记（近义>截断，更精确优先）
    merged: list[dict] = []
    for g in groups:
        for exist in merged:
            if g["names"] & exist["names"]:
                exist["names"] |= g["names"]
                if g["flag"] == "近义":
                    exist["flag"] = "近义"
                # 吸收组的强制目标在合并后仍需保留
                if g.get("_target"):
                    exist.setdefault("_target", g["_target"])
                break
        else:
            merged.append(g)
    for g in merged:
        # 吸收组（独立短词→复合词）强制用目标；普通组走次数+简体优先
        t = g.get("_target") or pick(list(g["names"]))
        set_target(list(g["names"]), t, g["flag"])
    return len(merged)


# ---------- 3. AI 分类（DashScope / Ollama 双引擎） ----------

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen3:8b"      # 默认本地模型（12G 显存安全）


def ai_classify(stats: TagStats, engine: str = "ollama", api_key: str = "",
                ollama_model: str = OLLAMA_MODEL, on_progress=None,
                cancel_check=None) -> dict:
    """对未决策的 tag 调用 AI 分类（keep/remove/merge+规范名）。

    engine: "ollama"（本地，无审查，推荐） / "dashscope"（云端，小批可用）
    显存策略：批间模型驻留提速，全部结束后立即卸载（keep_alive=0）。
    cancel_check()：返回 True 时提前结束（已分析部分保留），用于后台线程取消。
    返回诊断 dict：{"batches": int, "failed": int, "req_err": int}，供 UI 展示失败原因。
    """
    pending = [st for st in stats.tags.values() if not st.rule_flag and not st.sim_target]
    total = len(pending)
    done = 0
    BATCH = 120
    known = set(stats.tags)
    diag = {"batches": 0, "failed": 0, "req_err": 0}
    for i in range(0, total, BATCH):
        if cancel_check and cancel_check():
            break
        batch = pending[i:i + BATCH]
        diag["batches"] += 1
        if engine == "dashscope":
            result = _ai_one_batch_dashscope(batch, api_key)
        else:
            result = _ai_one_batch_ollama(batch, ollama_model)
        if not result:
            diag["failed"] += 1
        done += len(batch)
        if on_progress:
            on_progress(done, total, batch[-1].name)
        for st in batch:
            r = result.get(st.name)
            if not r:
                continue
            act = (r.get("action") or "keep").lower()
            if act == "remove":
                st.ai_action = "remove"
                st.ai_reason = r.get("reason", "")
            elif act == "merge":
                t = (r.get("target") or "").strip()
                # 校验：目标必须在真实标签列表中，且不能指向自己，防止 AI 自创新词
                if t and t in known and t != st.name:
                    # 方向校正：AI 只懂语义不懂次数/简繁，向「主流+简体」写法收敛
                    t = _resolve_ai_target(stats, st.name, t)
                    st.ai_action = "merge"
                    st.ai_target = t
                    st.ai_reason = r.get("reason", "")
                else:
                    st.ai_action = "keep"
                    st.ai_reason = f"合并目标无效({t or '空'})，改为保留"
            else:
                st.ai_action = "keep"
    # 全部结束：卸载本地模型，释放显存
    if engine != "dashscope":
        _unload_ollama(ollama_model)
    return diag


def _build_prompt(batch: list[TagStat]) -> str:
    lines = []
    for st in batch:
        lines.append(f"- {st.name}（出现 {st.count} 次）")
    tag_list = "\n".join(lines)
    return f"""你是视频媒体库的标签管理助手。下面是从 NFO 元数据中收集的「标签/类型」词条列表（括号内是出现次数）。这些词条来自用户收藏的视频库，质量参差，需要整理归类。

你的任务：逐一判断每个词条：
- keep：正常的内容描述词（题材、画质、身体特征、剧情元素、身份属性等），保留
- remove：不属于内容描述的词——具体人名/艺名、公司名、发行方、系列名、导演名、编号、纯数字、网址、平台名、广告宣传语、乱码等。这些应放在 NFO 的 actor/studio/series 等专门字段，不应出现在标签里
- merge：与列表中另一个词条含义相同或相近（不同写法、简繁体、中英文、别名），合并到那个更常用的词条。target 必须是列表中真实存在的某个词条名（选出现次数最多的规范写法），不要自创新词。**次数接近时，优先简体中文写法**（如 顔射/颜射 合并到 颜射、無碼/无码 合并到 无码）

【日文/中文变体特别注意】本库标签很多是日文原词与中文/简体/繁体混用，以下情况必须判为 merge（同义），而不是 remove：
- 日文汉字与简体中文：顔射/颜射、無碼/无码、中出/中出し、女体盛/女体盛り、素人/素人娘
- 片假名与平假名：ハメ撮り/はめ撮り
- 长音/浊音/送假名差异：制服/制コス、SM/エスエム、パイパン/パイパン
- 中文简繁：喷出/噴出、口爆/口爆、乳首/乳头
只有**同一概念的不同写法**才合并；两个含义确实不同（如 素人/熟女）绝不合并。

【合并纪律】只合并明确同义/近义的词条，不要发散地把相近但不同的概念合并（例如 美少女 和 美少女系 可合并，但 美少女 和 少女 不合并）。

【删除纪律】拿不准的词条优先 keep 或 merge 到最接近的词条，只有确凿不属于内容标签（人名/片商/系列/编号/乱码）才 remove。
典型的题材/身体/场景词——寝取られ、中出し、顔射、フェラ、素人、巨乳、制服、露出、痴女、人妻、熟女、美少女、ハメ撮り 等——即使出现次数少也是有效内容标签，应 keep（有同义写法则 merge），不要因为低频或看不懂就 remove。日语词条中，只有**人名/艺名、片商名、系列名、番号**这类才 remove。

直接输出 JSON，不要输出任何多余文字，不要 markdown 代码块：
{{"tags": {{"词条名": {{"action": "keep|remove|merge", "target": "合并目标(仅merge时填写)", "reason": "简短归类理由"}}}}}}

待分类的词条：
{tag_list}
"""


def _ai_one_batch_dashscope(batch: list[TagStat], api_key: str) -> dict:
    """DashScope 通义千问（质量好，但输入审查可能 400；失败返回 {} 由调用方降级）。"""
    import requests
    prompt = _build_prompt(batch)
    try:
        r = requests.post(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": "qwen-plus",
                "input": {"messages": [{"role": "user", "content": prompt}]},
                "parameters": {"temperature": 0.1, "result_format": "message"},
            },
            timeout=90,
        )
        if r.status_code != 200:
            return {}
        data = r.json()
        content = data["output"]["choices"][0]["message"]["content"]
        return _parse_ai_json(content)
    except Exception:  # noqa: BLE001
        return {}


def _ai_one_batch_ollama(batch: list[TagStat], model: str = OLLAMA_MODEL) -> dict:
    """Ollama 本地模型（无审查）。批间驻留（keep_alive 5m）、seed 固定保证可复现。"""
    import requests
    prompt = _build_prompt(batch)
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat",
                          json={"model": model,
                                "messages": [{"role": "user", "content": prompt}],
                                "stream": False,
                                "options": {"temperature": 0.1, "seed": 42},
                                "keep_alive": "5m"},
                          timeout=300, proxies={})
        if r.status_code != 200:
            return {}
        content = r.json().get("message", {}).get("content", "")
        return _parse_ai_json(content)
    except Exception:  # noqa: BLE001
        return {}


def _unload_ollama(model: str = OLLAMA_MODEL, timeout: float = 30.0) -> None:
    """立即卸载本地模型释放显存（keep_alive=0）。

    timeout 可调小（如 6s）用于应用退出场景：宁可放弃本次卸载也不能卡死退出。
    传入 proxies={} 禁走系统代理（用户环境配有 Clash，避免卸载请求被代理拦截慢响应）。
    返回 None；任何异常吞掉（卸载失败不影响主流程）。
    """
    import requests
    try:
        requests.post(f"{OLLAMA_URL}/api/chat",
                      json={"model": model,
                            "messages": [{"role": "user", "content": "ok"}],
                            "stream": False, "keep_alive": 0},
                      timeout=timeout, proxies={})
    except Exception:  # noqa: BLE001
        pass


def ollama_available() -> bool:
    """检测本地 Ollama 服务是否可用。"""
    import requests
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3, proxies={})
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def ai_reclassify_censorship(markers: list[str], model: str = OLLAMA_MODEL,
                             batch: int = 100, on_progress=None) -> dict:
    """用本地模型复核「有码/无码」：对关键词表判不出的标记，让模型归类。

    返回 {marker: "censored" | "uncensored" | "other"}。
    只读建议；结果由调用方决定是否采纳。批间驻留提速，结束卸载释放显存。
    """
    import requests
    known = list(CENSORED_KEYWORDS)
    known_u = list(UNCENSORED_KEYWORDS)
    result: dict = {}
    total = len(markers)
    done = 0
    try:
        for i in range(0, total, batch):
            chunk = markers[i:i + batch]
            lines = "\n".join(f"- {m}" for m in chunk)
            prompt = (f"你是成人视频库的标签助手。下面是某视频【标签/类型】里我无法用关键词判定的词条。"
                      f"请判断每个词条表达的是视频的『打码状态』：\n"
                      f"- censored：表示『有码/打码』（如 有码、马赛克、骑兵、碼、モザイクあり 等）\n"
                      f"- uncensored：表示『无码/破解/流出』（如 无码、無碼、破解、流出、步兵、无修、"
                      f"無修正、オンリー無修正 等）\n"
                      f"- other：纯内容标签（题材/身体/剧情，如 巨乳、中出、素人），不涉及打码状态\n\n"
                      f"已知有码词：{'、'.join(known)}；已知无码词：{'、'.join(known_u)}。"
                      f"只把确实表达打码状态的词判为 censored/uncensored，其余一律 other。\n"
                      f"逐个输出 JSON：{{\"tags\": {{\"词条\": \"censored|uncensored|other\"}}}}，"
                      f"不要多余文字、不要 markdown 代码块。\n\n待判断：\n{lines}")
            r = requests.post(f"{OLLAMA_URL}/api/chat",
                              json={"model": model,
                                    "messages": [{"role": "user", "content": prompt}],
                                    "stream": False,
                                    "options": {"temperature": 0.05, "seed": 42},
                                    "keep_alive": "5m"},
                              timeout=300, proxies={})
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
            if r.status_code != 200:
                continue
            content = r.json().get("message", {}).get("content", "")
            tags = _parse_flat_json(content)
            for m in chunk:
                v = str(tags.get(m, "")).lower() if isinstance(tags, dict) else ""
                if v in ("censored", "uncensored", "other"):
                    result[m] = v
    finally:
        _unload_ollama(model)
    return result


def _parse_flat_json(content: str) -> dict:
    """容错解析「{词条: 字符串值}」的 AI JSON（保留字符串值）。"""
    if not content:
        return {}
    text = re.sub(r"^```(?:json)?\s*", "", content.strip())
    text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return {}
    import json as _json
    try:
        data = _json.loads(text[s:e + 1])
    except _json.JSONDecodeError:
        return {}
    tags = data.get("tags") if isinstance(data, dict) and "tags" in data else data
    if not isinstance(tags, dict):
        return {}
    return {str(k): v for k, v in tags.items()}


def ollama_has_model(model: str = OLLAMA_MODEL) -> bool:
    import requests
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5, proxies={})
        if r.status_code != 200:
            return False
        names = [m.get("name", "") for m in r.json().get("models", [])]
        return any(model in n for n in names)
    except Exception:  # noqa: BLE001
        return False


def _parse_ai_json(content: str) -> dict:
    """容错解析 AI 输出中的 JSON。"""
    if not content:
        return {}
    text = content.strip()
    # 去掉 markdown 代码块
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # 找第一个 { 和最后一个 }
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return {}
    try:
        data = json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return {}
    tags = data.get("tags") or data
    if not isinstance(tags, dict):
        return {}
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in tags.items()}


# ---------- 3.5 AI 结果持久化（重开/重复整理不丢 AI 决策） ----------

def _default_log_dir() -> str:
    """默认日志目录：优先数据目录下的 logs（与 AppLogger 一致）。"""
    try:
        from core.appdirs import resolve_data_dir
        return str(resolve_data_dir()[0] / "logs")
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".nfo-tag-fixer", "logs")


def ai_result_path(folders: list[str], log_dir: str = "") -> str:
    """AI 结果缓存文件路径：按目录生成哈希，同一批素材复用同一份。"""
    import hashlib
    key = "|".join(os.path.abspath(f) for f in sorted(folders) if f)
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
    base = log_dir or _default_log_dir()
    return os.path.join(os.path.abspath(base), f"organize_ai_{digest}.json")


def save_ai_result(stats: TagStats, path: str) -> str:
    """把 AI 决策保存为 JSON：{tag: {action, target, reason}}。返回写入路径。"""
    data = {}
    for st in stats.tags.values():
        if not st.ai_action:
            continue
        item = {"action": st.ai_action}
        if st.ai_target:
            item["target"] = st.ai_target
        if st.ai_reason:
            item["reason"] = st.ai_reason
        data[st.name] = item
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path


def load_ai_result(stats: TagStats, path: str) -> int:
    """把缓存 JSON 的 AI 决策回填到 stats（仅当标签仍存在、目标仍存在才应用）。

    返回回填的标签数。缺失/失效的决策忽略，保留给人工处理。
    """
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0
    known = set(stats.tags)
    n = 0
    for name, item in data.items():
        st = stats.tags.get(name)
        if st is None or st.rule_flag or st.sim_target or st.user_action:
            continue
        if not isinstance(item, dict):
            continue
        act = (item.get("action") or "").lower()
        if act == "remove":
            st.ai_action = "remove"
            st.ai_reason = item.get("reason", "")
            n += 1
        elif act == "merge":
            t = (item.get("target") or "").strip()
            if t in known and t != name:
                # 缓存回填同样做方向校正（与 ai_classify 一致，防止旧结果方向错乱）
                t = _resolve_ai_target(stats, name, t)
                st.ai_action = "merge"
                st.ai_target = t
                st.ai_reason = item.get("reason", "")
                n += 1
        elif act == "keep":
            st.ai_action = "keep"
            n += 1
    return n


# ---------- 4. 执行 ----------

def apply_organize(nfo_files: list[str], mapping: dict,
                   backup: bool = True, on_progress=None,
                   cancel_check=None) -> tuple[int, list]:
    """按 mapping 删除/合并 NFO 的 tag/genre。

    mapping: {old_name: {"action": "remove"} | {"action": "merge", "target": "x"}}
    on_progress(done, total)：每处理一个文件回调（增量计数），用于后台进度条。
    cancel_check()：返回 True 时提前结束（返回已处理部分），用于后台线程取消。
    返回 (成功数, [(path, error)]).
    """
    ok, errs = 0, []
    total = len(nfo_files) or 1
    for idx, p in enumerate(nfo_files):
        if cancel_check and cancel_check():
            break
        try:
            nfo = NfoFile(p)
            if not nfo.is_valid:
                continue
            changed = _apply_mapping_to_nfo(nfo, mapping)
            if not changed:
                continue
            e = nfo.save(backup=backup)
            if e:
                errs.append((p, e))
            else:
                ok += 1
        except Exception as e:  # noqa: BLE001
            errs.append((p, str(e)))
        if on_progress and (idx % 50 == 0 or idx == total - 1):
            on_progress(idx + 1, total)
    if on_progress:
        on_progress(total, total)
    return ok, errs


def _apply_mapping_to_nfo(nfo: NfoFile, mapping: dict) -> int:
    """对单个 NFO 应用映射，返回变更的元素数。"""
    if not nfo.is_valid or nfo.root is None:
        return 0
    changed = 0
    for f in ("tag", "genre"):
        for el in list(nfo.root.iter(f)):
            t = (el.text or "").strip()
            rule = mapping.get(t)
            if not rule:
                continue
            act = rule.get("action")
            if act == "remove":
                nfo.root.remove(el)
                changed += 1
            elif act == "merge":
                target = rule.get("target", "").strip()
                if target:
                    el.text = target
                    changed += 1
    if changed:
        nfo.mark_dirty()
    return changed


def effective_mapping(stats: TagStats) -> dict:
    """汇总用户决策（规则/AI/同义检测/用户覆盖）为执行映射。"""
    mapping = {}
    for st in stats.tags.values():
        if st.user_action:
            action = st.user_action
        elif st.ai_action:
            action = st.ai_action
        elif st.rule_flag:
            action = "remove"
        elif st.sim_target:
            action = "merge"
        else:
            action = "keep"
        if action == "remove":
            mapping[st.name] = {"action": "remove"}
        elif action == "merge":
            target = (st.user_target or st.ai_target or st.sim_target or "").strip()
            if target and target != st.name:
                mapping[st.name] = {"action": "merge", "target": target}
    return mapping


def merge_sources(mapping: dict) -> dict:
    """按 target 统计哪些旧标签被合并过来（供确认展示）。"""
    src = defaultdict(list)
    for old, rule in mapping.items():
        if rule.get("action") == "merge":
            src[rule["target"]].append(old)
    return dict(src)


# ---------- 5. 备份恢复 ----------

def list_backups(folders: list[str]) -> list[dict]:
    """递归扫描目录树中的 .nfo.bak 备份文件。

    返回 [{path: nfo路径, bak: 备份路径, mtime: 修改时间戳, size: 字节}]
    按修改时间倒序（最近的整理批次在前）。
    """
    found = []
    for d in folders:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith(".nfo.bak"):
                    continue
                bak = os.path.join(root, fn)
                nfo = bak[:-4]
                if not os.path.exists(nfo):
                    continue  # 原文件已被删除，跳过
                try:
                    st = os.stat(bak)
                    found.append({"path": nfo, "bak": bak,
                                  "mtime": st.st_mtime, "size": st.st_size})
                except OSError:
                    continue
    found.sort(key=lambda x: x["mtime"], reverse=True)
    return found


def restore_backups(backups: list[dict]) -> tuple[int, list]:
    """把 .bak 备份恢复为 NFO（覆盖当前 .nfo）。

    恢复前将当前 .nfo 另存为 .bak.pre_restore（防止恢复错了丢失现状）。
    返回 (成功数, [(path, error)]).
    """
    ok, errs = 0, []
    for b in backups:
        nfo_path = b.get("path") or b.get("bak", "")[:-4]
        bak = b.get("bak", nfo_path + ".bak")
        try:
            if not os.path.exists(bak):
                errs.append((nfo_path, "备份文件不存在"))
                continue
            # 先保险：当前文件另存一份
            if os.path.exists(nfo_path):
                shutil.copy2(nfo_path, nfo_path + ".bak.pre_restore")
            shutil.copy2(bak, nfo_path)
            ok += 1
        except OSError as e:
            errs.append((nfo_path, str(e)))
    return ok, errs


# ---------- 6. 变更报告 ----------

def build_report(stats: TagStats, mapping: dict) -> dict:
    """生成整理方案/结果报告：
    merges: [{old, target, count, flag, reason}]（old→target 合并清单）
    removes: [{name, count, flag, reason}]
    keeps: 保留数量（未在 mapping 中的标签）
    nfo_count / tag_total
    """
    merges, removes = [], []
    for st in stats.tags.values():
        rule = mapping.get(st.name)
        flag = st.rule_flag or ("近义" if st.sim_target else "")
        if rule is None:
            continue
        if rule["action"] == "remove":
            removes.append({"name": st.name, "count": st.count, "flag": flag,
                            "reason": st.ai_reason})
        elif rule["action"] == "merge":
            merges.append({"old": st.name, "target": rule["target"],
                           "count": st.count, "flag": flag, "reason": st.ai_reason})
    merges.sort(key=lambda x: (-x["count"], x["old"]))
    removes.sort(key=lambda x: (-x["count"], x["name"]))
    kept = sum(1 for st in stats.tags.values() if st.name not in mapping)
    return {"merges": merges, "removes": removes, "keeps": kept,
            "nfo_count": len(stats.nfo_files), "tag_total": len(stats.tags)}


def save_report_html(path: str, report: dict) -> str:
    """把报告保存为可读 HTML（供日后核查）。返回写入路径。"""
    from datetime import datetime
    def esc(value) -> str:
        """HTML 转义（报告里会回显用户标签，必须转义）。"""
        return (str(value).replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;"))
    rows_m = "".join(
        f"<tr><td>{esc(x['old'])}</td><td class='arr'>→</td><td>{esc(x['target'])}</td>"
        f"<td class='c'>{x['count']}</td><td class='c'>{esc(x['flag'])}</td></tr>"
        for x in report["merges"]) or "<tr><td colspan='5' class='empty'>无</td></tr>"
    rows_r = "".join(
        f"<tr><td>{esc(x['name'])}</td><td class='c'>{x['count']}</td>"
        f"<td class='c'>{esc(x['flag'])}</td><td>{esc(x['reason'])}</td></tr>"
        for x in report["removes"]) or "<tr><td colspan='4' class='empty'>无</td></tr>"
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>标签整理变更报告</title><style>
body{{font-family:"Microsoft YaHei",sans-serif;max-width:860px;margin:24px auto;color:#222;background:#fff}}
h1{{font-size:20px}} h2{{font-size:15px;margin-top:22px;border-bottom:1px solid #eee;padding-bottom:6px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{border:1px solid #e5e5e5;padding:4px 8px;text-align:left}}
th{{background:#f5f6f8}} .c{{text-align:center;width:60px}} .arr{{color:#c00;text-align:center;width:30px}}
.empty{{color:#999;text-align:center}}
.sum{{background:#f0f7ff;border:1px solid #cfe3ff;padding:10px 14px;border-radius:6px;margin:12px 0}}
</style></head><body>
<h1>标签整理变更报告</h1>
<div class="sum">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ｜
涉及 {report['nfo_count']} 个 NFO ｜ 标签总数 {report['tag_total']} ｜
合并 {len(report['merges'])} 组 ｜ 删除 {len(report['removes'])} 种 ｜ 保留 {report['keeps']} 种</div>
<h2>合并 / 改名（{len(report['merges'])} 组）</h2>
<table><tr><th>原标签</th><th></th><th>合并到</th><th class="c">次数</th><th class="c">依据</th></tr>{rows_m}</table>
<h2>删除（{len(report['removes'])} 种）</h2>
<table><tr><th>标签</th><th class="c">次数</th><th class="c">依据</th><th>原因</th></tr>{rows_r}</table>
</body></html>"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# ---------- 8. 稀疏标签补全：从标题用 AI 挖类型标签 ----------

# 生成/补全阶段禁止写入的标签（保持 tag 干净、可检索）
_TAG_WRITE_BLOCK_RE = [
    re.compile(r"^(片商|发行|系列|导演|编号|レーベル|メーカー|発行|公开|出品)[:：]"),
    re.compile(r"^[\d\W]+$"),                 # 纯数字/纯符号
    re.compile(r"\d{4}P\b", re.I),            # 720P/1080P 等画质
    re.compile(r"^(\d[\d.-]*|[\u30a0-\u30ff]+)$"),  # 纯数字/纯假名(无实义)
]


def _tag_write_ok(t: str) -> bool:
    t = (t or "").strip()
    if not t or len(t) > 14 or len(t) < 2:
        return False
    low = t.lower()
    if low in ("fc2", "ppv", "hd", "4k", "h264", "hevc", "sdr", "hdr",
               "无码", "有码", "破解", "流出", "中文字幕", "字幕"):
        return False
    if any(rx.search(t) for rx in _TAG_WRITE_BLOCK_RE):
        return False
    return True


def _parse_title_tags(content: str) -> dict:
    """解析「标题 → 标签列表」的 AI JSON。

    兼容三种输出形态（qwen3 偶尔会换格式）：
    - {"tags": [["标题", ["标签", ...]], ...]}            list 对
    - {"tags": [{"title": "标题", "tags": [...]}, ...]}   dict 列表
    - {"标题": ["标签", ...]}                              dict 直出
    """
    if not content:
        return {}
    text = re.sub(r"^```(?:json)?\s*", "", content.strip())
    text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return {}
    import json as _json
    try:
        data = _json.loads(text[s:e + 1])
    except _json.JSONDecodeError:
        return {}
    tags = data.get("tags", data)
    out: dict = {}
    if isinstance(tags, dict):
        for k, v in tags.items():
            if isinstance(v, list):
                out[str(k)] = [str(x) for x in v if isinstance(x, str)]
    elif isinstance(tags, list):
        for pair in tags:
            if isinstance(pair, list) and len(pair) == 2:
                out[str(pair[0])] = [str(x) for x in pair[1]
                                     if isinstance(x, str)]
            elif isinstance(pair, dict):
                t = (pair.get("title") or pair.get("name")
                     or pair.get("标题") or "").strip()
                ts = (pair.get("tags") or pair.get("tag")
                      or pair.get("labels") or [])
                if t and isinstance(ts, list):
                    out[t] = [str(x) for x in ts if isinstance(x, str)]
    return out


def _match_title(mined: dict, key: str) -> list:
    """按标题取 AI 挖出的标签：精确 → 归一化 → 双向包含。

    AI 返回的「标题原文」常与输入有细微出入（标点/空格/截断/加序号/改写），
    纯精确匹配会导致全部落空——这里做多层匹配兜底。
    key 可能带序号前缀（LLM 输出 "1. FC2-..."），先剥离再匹配。
    """
    def _norm(s: str) -> str:
        # 剥离序号前缀（"1. " / "1、" / "- " 等）
        s = re.sub(r"^\d+[\s.、:：\-－]+", "", (s or "").strip())
        return norm_key(s)

    if not key:
        return []
    k = _norm(key)
    # 1) 精确（已归一化的 key 对原始 key，先原样试一次）
    if key in mined:
        return mined[key]
    # 2) 归一化后相等（剥离序号后再比）
    found = None
    for kk, v in mined.items():
        if k and _norm(kk) == k:
            found = v
            break
    if found is not None:
        return found
    # 3) 双向包含（AI 截断长标题或加了前缀后缀），归一化长度 ≥4 才认，防误配
    for kk, v in mined.items():
        kn = _norm(kk)
        if kn and k and min(len(kn), len(k)) >= 4 and (k in kn or kn in k):
            return v
    return []


def _align_to_vocab(tag: str, vocab: list[str]) -> str:
    """把新挖的标签对齐到「现有标签」词表：
    - 完全一致 → 直接用现有词
    - 归一化（简繁/假名/长音符/标点）后一致 → 用现有规范词（接轨）
    - 否则返回原标签（不牺牲可识别的标签，也不硬凑错词）。
    """
    if tag in vocab:
        return tag
    nk = norm_key(tag)
    for v in vocab:
        if norm_key(v) == nk:
            return v
    return tag


def ai_extract_type_tags(candidates: list[dict], model: str = OLLAMA_MODEL,
                         batch: int = 10, vocab: list | None = None,
                         on_progress=None, cancel_check=None,
                         pause_check=None) -> dict:
    """从标题/文件名用本地 AI 挖「类型标签」，给稀疏 tag（尤指 FC2 个人品）补区分度。

    candidates: [{path, name, title, tags:set}]  （tags=已存在的 tag/genre 集合）
    vocab:     现有标签词表（内容类）。**不再塞进 prompt**（实测会带偏模型、且拖慢），
               仅在 AI 输出后做归一化对齐接轨（_align_to_vocab），保证新词与本库一致。
    cancel_check(): 返回 True 时提前结束（用于后台取消）。
    pause_check():  批间调用（阻塞等待恢复，保持模型驻留），用于暂停/继续。
    返回 {path: [需要新增的规范类型标签]}（已过滤 已存在/噪声/前缀/人名 类）。
    只读建议；调用方确认后再写入（应带 .bak 备份）。
    单批异常隔离：某批失败只记录错误继续下一批，防止整体中断。

    本轮性能/精度教训（实测校正）：
    - qwen3:8b 默认会「思考」（输出 <think> 长文）→ 用「不要思考」压制，避免思考文本
      挤占输出预算、拖慢单批耗时（实测单批从 ~70s 降到 ~25s）。
    - 词表塞进 prompt 会带偏模型、不忠实回传标题（标题被 AI 改写）→ 纯标题提取，
      后处理接轨词表。
    - 标题用「编号强绑定」（N. 标题），并要求输出"标题原样保留"，让 _match_title 可靠命中。
    - 去掉「切半重试」（大 batch 会压崩 Ollama；且禁思考后 JSON 不再截断，无需重试）。
    """
    import requests
    import sys
    vocab_list = list(vocab) if vocab else []
    # dict 子类：允许挂 .stats 诊断属性，行为与普通 dict 完全一致
    class TagExtractResult(dict):
        pass
    out: dict = TagExtractResult()
    # 诊断统计：挂在返回值上（TagExtractResult 是 dict 子类，可带属性）
    diag = {"batches": 0, "req_err": 0, "http_err": 0, "stall": 0, "parse_fail": 0,
            "retry": 0, "ai_sample": None,
            "no_match": 0, "matched": 0, "new_tags": 0}
    total = len(candidates)
    done = 0

    # 单批请求：流式接收 + 智能首token超时（防卡死死等 300s） + num_ctx 降至 4096。
    # 返回 ("ok", content) / ("err", 异常信息) / ("http", 状态码) / ("stall", 说明)
    def _ask(chunk: list, first_token_timeout: float = 60.0) -> tuple[str, str]:
        # 编号标题强绑定：N. 标题（LLM 必须按编号对应输出原样标题，禁止改写/编造）
        lines = "\n".join(f"{i + 1}. {c['title'] or c['name']}" for i, c in enumerate(chunk))
        prompt = (
            "你是成人视频库的标签补全助手。下面是一批很‘缺标签’的视频标题"
            "（多为 FC2 素人自拍）。请为每个标题提取能表达【内容类型/题材/身体特征/"
            "动作情景】的类型标签（2-4 个），用于之后按类型检索筛选。\n"
            "要求：\n"
            "1. **标题必须原样保留**：输出里的标题要逐字使用下面第 N 条的内容，"
            "禁止改写、禁止加序号、禁止编造新标题。\n"
            "2. 只要类型词（题材/身体/动作）；不要人名、番号、系列名、日期、广告语、画质。\n"
            "3. 用词宜规范直白（如 美少女/中出/素人/巨乳/フェラ/ハメ撮り/熟女），"
            "可中英日混用，优先准确识别。\n"
            "4. 只输出一个 JSON 对象，不要多余文字、不要 markdown、不要思考过程：\n"
            '{"tags": [["<第1条标题原文>", ["标签1", "标签2"]], '
            '["<第2条标题原文>", ["标签1", "标签2"]], ...]}\n\n'
            "【标题列表】\n" + lines)
        try:
            # 流式接收：能实时看到 token 产出。首 token 超时判定卡死，避免死等 300s。
            # think=False：API 级压制 qwen3 的思考模式（比 prompt 提示"不要思考"更可靠）。
            with requests.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "stream": True,
                          "think": False,
                          "options": {"temperature": 0.1, "seed": 42,
                                      "num_ctx": 4096, "num_predict": 1024},
                          "keep_alive": "5m"},
                    timeout=(first_token_timeout, 300), stream=True,
                    proxies={}) as r:
                if r.status_code != 200:
                    print(f"[ai_extract_type_tags] HTTP {r.status_code}", file=sys.stderr)
                    return ("http", r.status_code)
                # 流式解析：累积第一次出现的非空 content（thinking 被压制后 cotent 直接有值）。
                buf = []
                first_byte_t = None
                t0 = time.time()
                import json as _json
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        d = _json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if "error" in d:
                        return ("err", str(d.get("error")))
                    msg = d.get("message", {})
                    piece = msg.get("content") if isinstance(msg, dict) else None
                    if piece:
                        if first_byte_t is None:
                            first_byte_t = time.time() - t0
                        buf.append(piece)
                content = "".join(buf)
                # 全程无输出 → 卡死判定
                if first_byte_t is None:
                    return ("stall", f"{time.time()-t0:.0f}s 无输出")
                return ("ok", content)
        except Exception as e:  # noqa: BLE001
            print(f"[ai_extract_type_tags] request failed: {e}", file=sys.stderr)
            return ("err", str(e))

    try:
        for i in range(0, total, batch):
            if cancel_check and cancel_check():
                break
            # 暂停：批间阻塞等待恢复（不消耗算力），若期间被取消则放弃
            if pause_check:
                pause_check()
            if cancel_check and cancel_check():
                break
            diag["batches"] += 1
            chunk = candidates[i:i + batch]
            if on_progress:
                on_progress(done, total, f"[{done}/{total}] 第 {diag['batches']} 批请求中…")
            st, content = _ask(chunk)
            if st != "ok":
                # 失败隔离：记录错误，跳过该批继续（不中断整体）。
                # stall = 首token超时后无输出（Ollama 卡死/模型未就绪），单独统计。
                if st == "stall":
                    diag["stall"] += 1
                else:
                    diag["req_err" if st == "err" else "http_err"] += 1
                done += len(chunk)
                if on_progress:
                    on_progress(done, total,
                                f"[{done}/{total}] 批次失败（{content}，已跳过）")
                continue
            mined = _parse_title_tags(content)
            if not mined and diag["ai_sample"] is None:
                diag["ai_sample"] = content[:400]  # 留样便于排查（不再切半重试）
            done += len(chunk)
            if not mined:
                diag["parse_fail"] += 1
            batch_new = 0
            for c in chunk:
                key = (c.get("title") or "").strip() or (c.get("name") or "")
                tags_from_ai = _match_title(mined, key)
                if not tags_from_ai:
                    diag["no_match"] += 1
                    continue
                diag["matched"] += 1
                new = []
                for t in tags_from_ai:
                    if t in c["tags"] or not _tag_write_ok(t):
                        continue
                    # 新词向现有词表接轨（仅归一化对齐，不因词表而改错词）
                    new.append(_align_to_vocab(t, vocab_list))
                new = [t for t in dict.fromkeys(new) if t not in c["tags"]]
                # 按标题匹配可能命中同一标题的多个文件（FC2 重复上传），统一补
                if new:
                    out[c["path"]] = new
                    batch_new += len(new)
            diag["new_tags"] += batch_new
            if on_progress:
                on_progress(done, total,
                            f"[{done}/{total}] {len(chunk)} 条 / 本批新增 {batch_new} 个标签"
                            + (f"，{len(mined)} 条已解析" if mined
                               else "，本批解析失败（已记录样本）"))
    finally:
        _unload_ollama(model)
    out.stats = diag
    return out


def apply_appended_tags(file_paths: list[str],
                        proposed: dict[str, list[str]]) -> tuple[int, list]:
    """把按路径补全的标签追加写入对应 NFO 的 <tag>（去重），保存时 .bak 备份。

    proposed: {path: [新标签]}。返回 (成功数, [(path, 错误)])。
    """
    ok, errs = 0, []
    from core.nfo import NfoFile
    for p in file_paths:
        tags = proposed.get(p)
        if not tags:
            continue
        try:
            nfo = NfoFile(p)
            if not nfo.is_valid:
                continue
            have = set(nfo.tags)
            to_add = [t for t in tags if t not in have]
            if not to_add:
                continue
            import xml.etree.ElementTree as ET
            for t in to_add:
                el = ET.SubElement(nfo.root, "tag")
                el.text = t
            nfo.mark_dirty()
            e = nfo.save(backup=True)
            if e:
                errs.append((p, e))
            else:
                ok += 1
        except Exception as e:  # noqa: BLE001
            errs.append((p, str(e)[:80]))
    return ok, errs
