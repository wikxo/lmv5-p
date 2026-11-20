# text_processing.py
from __future__ import annotations

from collections import Counter
import re
from typing import Final, Literal, Sequence

URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"https?://\S+")
DISCORD_MENTION_PATTERN: Final[re.Pattern[str]] = re.compile(r"<[@#!&]+?\d+>|a?:[a-zA-Z0-9_]+:\d+>")
WHITESPACE_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s+")
CLAUSE_SPLIT_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\n\r，。！？、；,!?;~～]+")
CJK_BLOCK_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\u4e00-\u9fff]{2,8}")
ENGLISH_WORD_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-zA-Z0-9_]{2,20}")

SIMPLIFIED_MARKERS: Final[frozenset[str]] = frozenset(
    "这们个为国发会经动进着种开实对学现样话没见点起头定"
    "问尔与专业东丝两严丧个丰临为丽举么义乌乐乔习乡书买乱"
)
TRADITIONAL_MARKERS: Final[frozenset[str]] = frozenset(
    "這們個為國發會經動進著種開實對學現樣話沒見點起頭定"
    "問爾與專業東絲兩嚴喪個豐臨為麗舉麼義烏樂喬習鄉書買亂"
)

STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "的", "了", "是", "我", "你", "他", "她", "它", "我們", "你們", "他們",
        "这个", "這個", "那个", "那個", "什么", "什麼", "怎么", "怎麼",
        "可以", "因為", "因为", "所以", "如果", "但是", "就是", "還是", "还是",
        "沒有", "没有", "一個", "一个", "知道", "覺得", "觉得", "現在", "现在",
        "這樣", "这样", "那樣", "那样", "自己", "可能", "應該", "应该", "已經",
        "已经", "一直", "一下", "一樣", "一样", "今天", "明天", "昨天",
        "the", "and", "for", "that", "this", "with", "you", "not", "are", "from",
    }
)


def truncate_message_content(content: str, max_chars: int = 300) -> str:
    cleaned = WHITESPACE_PATTERN.sub(" ", content).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars]}... [truncated]"


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 3)


def detect_chinese_script(text: str) -> Literal["zh-TW", "zh-CN"]:
    simp_hits = sum(1 for ch in text if ch in SIMPLIFIED_MARKERS)
    trad_hits = sum(1 for ch in text if ch in TRADITIONAL_MARKERS)
    if simp_hits > trad_hits and simp_hits >= 2:
        return "zh-CN"
    return "zh-TW"


def _clean_for_ngram(text: str) -> str:
    text = URL_PATTERN.sub("", text)
    text = DISCORD_MENTION_PATTERN.sub("", text)
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def extract_top_catchphrases(
    messages: Sequence[str],
    top_k: int = 15,
) -> list[tuple[str, int]]:
    """
    優先統計使用者最常發送的完整短句/子句（如「？」、「打瓦」、「笑死」、「上號」），
    再補充高頻中英詞彙，避免切出無意義的碎字。
    """
    clause_counter: Counter[str] = Counter()
    ngram_counter: Counter[str] = Counter()

    for raw_msg in messages:
        cleaned = _clean_for_ngram(raw_msg)
        if not cleaned:
            continue

        # 1. 完整短訊息直接計數（1 ~ 25 字）
        if 1 <= len(cleaned) <= 25 and cleaned.lower() not in STOPWORDS:
            clause_counter[cleaned] += 1

        # 2. 依標點符號切分子句計數（2 ~ 20 字）
        for clause in CLAUSE_SPLIT_PATTERN.split(cleaned):
            c = clause.strip()
            if 2 <= len(c) <= 20 and c != cleaned and c.lower() not in STOPWORDS:
                clause_counter[c] += 1

        # 3. 補充英文與中文字詞（僅在完整短句不足時作為後備）
        for word in ENGLISH_WORD_PATTERN.findall(cleaned.lower()):
            if len(word) >= 2 and word not in STOPWORDS:
                ngram_counter[word] += 1

        for block in CJK_BLOCK_PATTERN.findall(cleaned):
            if block not in STOPWORDS and 2 <= len(block) <= 6:
                ngram_counter[block] += 1

    results: list[tuple[str, int]] = []
    seen: set[str] = set()

    # 優先納入出現 >= 2 次的完整短句與子句
    for phrase, count in clause_counter.most_common(top_k * 3):
        if count < 2:
            break
        norm = phrase.lower()
        if norm in seen:
            continue
        seen.add(norm)
        results.append((phrase, count))
        if len(results) >= top_k:
            return results

    # 若完整重複短句不足，再由高頻詞彙補足
    for phrase, count in ngram_counter.most_common(top_k * 4):
        if count < 2:
            break
        norm = phrase.lower()
        if norm in seen or any(norm in existing.lower() for existing, _ in results):
            continue
        seen.add(norm)
        results.append((phrase, count))
        if len(results) >= top_k:
            break

    # 若該成員總發言極少且皆無重複，列出最近說過的代表句（次數標為 1）
    if not results:
        for raw_msg in messages:
            cleaned = _clean_for_ngram(raw_msg)
            if 1 <= len(cleaned) <= 30 and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                results.append((cleaned, 1))
                if len(results) >= min(top_k, 5):
                    break

    return results