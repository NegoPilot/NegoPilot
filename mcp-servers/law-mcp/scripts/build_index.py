"""data/raw 의 법령 Markdown을 조문 단위로 쪼개 data/index/*.json 으로 저장한다.

MCP 서버는 이 JSON 파일만 읽는다. 법령 원문을 새로 받으면 이 스크립트를 다시 실행한다.

사용법:
    uv run python scripts/build_index.py
"""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
INDEX_DIR = ROOT / "data" / "index"

ARTICLE_RE = re.compile(r"^#####\s+(제\d+조(?:의\d+)?)\s*(?:\((.*?)\))?\s*(.*)$")
CHAPTER_RE = re.compile(r"^##\s+(제\d+장.*)$")
SECTION_RE = re.compile(r"^###\s+(제\d+절.*)$")
ADDENDUM_RE = re.compile(r"^부칙")
PARAGRAPH_RE = re.compile(r"\*\*([①-⑳㉑-㉟㊱-㊿])\*\*")
# 원문이 16항 이상을 원문자 대신 "**<16>** <16>"으로 적는 판이 있다. 원문자로 바꿔 판마다 표기를 맞춘다
NUMBERED_PARAGRAPH_RE = re.compile(r"\*\*<(\d{1,2})>\*\*(?: <\1>)?")


def circled(n: int) -> str:
    """1~50을 원문자(①~㊿)로 바꾼다."""
    if 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    if 21 <= n <= 35:
        return chr(0x3251 + n - 21)
    if 36 <= n <= 50:
        return chr(0x32B1 + n - 36)
    raise ValueError(f"항 번호 {n}을 원문자로 바꿀 수 없다")


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm) or {}, body


def clean(text: str) -> str:
    """Markdown 이스케이프와 굵게 표시를 지우고 빈 줄을 정리한다."""
    text = text.replace("\\.", ".").replace("**", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_paragraphs(body: str) -> list[dict]:
    """조문 본문을 항(①, ② ...) 단위로 나눈다. 항이 없으면 본문 전체가 한 덩어리다."""
    parts = PARAGRAPH_RE.split(body)
    if len(parts) == 1:
        return [{"no": None, "text": clean(body)}]
    paragraphs = []
    if parts[0].strip():
        paragraphs.append({"no": None, "text": clean(parts[0])})
    for marker, text in zip(parts[1::2], parts[2::2]):
        paragraphs.append({"no": marker, "text": clean(text)})
    return paragraphs


def parse_body(body: str) -> tuple[list[dict], list[dict]]:
    articles, addenda = [], []
    chapter = section = None
    current = None
    in_addenda = False
    addendum = None

    def flush_article():
        if current is not None:
            text = NUMBERED_PARAGRAPH_RE.sub(lambda m: f"**{circled(int(m.group(1)))}**", "\n".join(current["lines"]))
            current["paragraphs"] = split_paragraphs(text)
            current["text"] = clean(text)
            # 삭제된 조문은 제목이나 본문이 "삭제 <날짜>" 한 줄뿐이다
            current["deleted"] = current["deleted"] or (
                current["text"].startswith("삭제") and len(current["text"]) < 40)
            del current["lines"]
            articles.append(current)

    for line in body.splitlines():
        if line.startswith("## 부칙"):
            flush_article()
            current = None
            in_addenda = True
            continue
        if in_addenda:
            if ADDENDUM_RE.match(line):
                if addendum:
                    addendum["text"] = clean("\n".join(addendum.pop("lines")))
                    addenda.append(addendum)
                addendum = {"title": line.strip(), "lines": []}
            elif addendum is not None:
                addendum["lines"].append(line)
            continue
        if m := CHAPTER_RE.match(line):
            chapter, section = m.group(1).strip(), None
            continue
        if m := SECTION_RE.match(line):
            section = m.group(1).strip()
            continue
        if m := ARTICLE_RE.match(line):
            flush_article()
            no, title, rest = m.group(1), m.group(2), m.group(3).strip()
            current = {"no": no, "title": title or rest or "", "chapter": chapter,
                       "section": section, "deleted": "삭제" in rest and not title,
                       "lines": [rest] if rest and title else []}
            continue
        if current is not None:
            current["lines"].append(line)

    flush_article()
    if addendum:
        addendum["text"] = clean("\n".join(addendum.pop("lines")))
        addenda.append(addendum)
    return articles, addenda


def build_law(law_key: str, info: dict) -> dict:
    versions = []
    for v in info["versions"]:
        text = (ROOT / v["file"]).read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        articles, addenda = parse_body(body)
        versions.append({
            "effective_date": str(fm.get("시행일자")),
            "promulgation_date": str(fm.get("공포일자")),
            "promulgation_no": str(fm.get("공포번호", "")),
            "law_name": fm.get("제목"),
            "law_type": fm.get("법령구분"),
            "ministry": fm.get("소관부처") or [],
            "source_url": fm.get("출처"),
            "raw_file": v["file"],
            "commit": v["commit"],
            "articles": articles,
            "addenda": addenda,
        })
    # 시행일 기준으로 정렬해 두면 서버가 "특정 날짜에 시행 중인 판"을 고르기 쉽다
    versions.sort(key=lambda v: (v["effective_date"], v["promulgation_date"]))
    return {"law_key": law_key, "law_name": versions[-1]["law_name"], "versions": versions}


def main() -> None:
    manifest = json.loads((RAW_DIR / "manifest.json").read_text(encoding="utf-8"))
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    for law_key, info in manifest["laws"].items():
        law = build_law(law_key, info)
        out = INDEX_DIR / f"{law_key}.json"
        out.write_text(json.dumps(law, ensure_ascii=False, indent=1), encoding="utf-8")
        latest = law["versions"][-1]
        print(f"{law_key}: {law['law_name']} | 판 {len(law['versions'])}개 | "
              f"최신판 조문 {len(latest['articles'])}개, 부칙 {len(latest['addenda'])}개")


if __name__ == "__main__":
    main()
