"""근거 검사와 인용 렌더링.

계약 8절에서 Generation이 맡은 "근거 없는 숫자·조문 차단"을 코드로 한다.
모델의 판단에 맡기지 않고 문자열로 비교하므로 비용이 없고 결과가 늘 같다.

검사 항목
1. 인용 id: 본문의 [E3]이 실제 근거 목록에 있고, 인용해도 되는 근거(citable)인가
2. 숫자: 단위가 붙은 숫자가 근거 본문이나 사용자 질문에 있는가. 금액은 값과 단위를 함께 비교한다
3. 조문 번호: 본문의 '제13조'가 근거 본문에 있는가

한계: 근거에 있는 숫자로 계산한 값(예: 12,000원의 5% 인하 → 11,400원)도 걸린다.
계산은 도구가 하고 모델은 옮겨 적기만 한다는 원칙이라 의도한 동작이다.
"""

import re
from collections.abc import Iterable

from negopilot_agent.generation.answer import Citation, Evidence, VerificationReport

# 근거 id. 모델이 항 번호를 붙여 [E6①]처럼 쓰는 경우가 있어 뒤의 원문자(①~⑳)도 인용으로 받는다 (#104)
CITE_ID = r"E\d+[\u2460-\u2473]*"
CITE_RE = re.compile(rf"\[\s*({CITE_ID}(?:\s*,\s*{CITE_ID})*)\s*\]")
PARAGRAPH_MARK_RE = re.compile(r"[\u2460-\u2473]+$")


def split_ids(group: str) -> list[str]:
    """'E6①, E6②, E4' → ['E6', 'E4']. 항 번호는 근거 id가 아니므로 떼고, 겹치면 한 번만 남긴다."""
    return list(dict.fromkeys(PARAGRAPH_MARK_RE.sub("", eid) for eid in re.split(r"\s*,\s*", group)))


# 긴 단위를 먼저 잡아 '3억원'을 '3억' 또는 '3원'으로 잘라 비교하지 않는다.
MONEY_UNIT = r"(?:(?:[십백천]\s*)?[경조억만](?:\s*원)?|[십백천]\s*원|원)"
NUMBER_WITH_UNIT_RE = re.compile(
    rf"(?<![A-Za-z0-9_.])(-?\d[\d,]*(?:\.\d+)?)\s*({MONEY_UNIT}|일|%|퍼센트|개|곳|ppm|배)")
MONEY_UNIT_RE = re.compile(MONEY_UNIT)
NON_MONEY_WITH_UNIT_RE = re.compile(r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)\s*(일|%|퍼센트|개|곳|ppm|배)")
NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
ARTICLE_RE = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?")
# 조 + 항: "제13조 제1항", "제13조제1항", "제13조 1항", "제13조①". 원문자는 ①~⑳, ㉑~㉟, ㊱~㊿
CIRCLED = "\u2460-\u2473\u3251-\u325f\u32b1-\u32bf"
ARTICLE_PARAGRAPH_RE = re.compile(
    rf"(제\s*\d+\s*조(?:\s*의\s*\d+)?)\s*(?:제\s*(\d+)\s*항|(\d+)\s*항|([{CIRCLED}]))")
PARAGRAPH_LINE_RE = re.compile(rf"^\s*([{CIRCLED}])", re.MULTILINE)
# business-mcp의 구조화된 금액 단위. 다른 숫자(연도/비율 등)는 금액 근거가 아니다.
# Evidence는 길이 제한으로 JSON 끝이 잘릴 수 있어 완성된 숫자 필드만 읽는다.
MONEY_FIELDS = {
    "revenue_eok": "억원",
    **dict.fromkeys(("revenue", "operating_income", "net_income", "total_assets", "total_liabilities",
                     "total_equity", "unit_price", "target_price", "last_price", "contract_amount"), "원"),
}
MONEY_FIELD_RE = re.compile(
    r'"(' + "|".join(MONEY_FIELDS) + r')"\s*:\s*(-?\d+(?:\.\d+)?)(?=\s*[,}\]])')
# business-mcp의 비율 필드. 화면에서는 100을 곱해 %로 쓰는 게 자연스럽다 (부채비율 1.85 → 185%).
# 0~1 비율은 아래 일반 규칙으로도 잡히지만, 부채비율처럼 1을 넘거나 반올림해 쓰는 경우는 필드로 읽어야 한다.
RATIO_FIELDS = ("debt_ratio", "operating_margin", "on_time_rate", "raw_material_ratio", "energy_ratio")
RATIO_FIELD_RE = re.compile(
    r'"(' + "|".join(RATIO_FIELDS) + r')"\s*:\s*(-?\d+(?:\.\d+)?)(?=\s*[,}\]])')
MASK = "[확인 필요]"


def canon(num: str) -> str:
    """'12,000' → '12000', '45.0' → '45', '-5' → '5'. 부호는 문장이 표현하므로(인하/인상) 뗀다."""
    s = num.replace(",", "").lstrip("-")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def known_numbers(texts: Iterable[str]) -> set[str]:
    nums: set[str] = set()
    for text in texts:
        for raw in NUMBER_RE.findall(text):
            c = canon(raw)
            nums.add(c)
            try:
                value = float(c)
            except ValueError:
                continue
            if 0 < value < 1:                    # 비율 0.45 → 45(%)
                nums.add(canon(f"{value * 100:.4f}"))
    return nums


def known_percentages(texts: Iterable[str]) -> set[str]:
    """비율 필드의 변환·반올림 값. % 단위 검사에서만 사용한다."""
    nums: set[str] = set()
    for text in texts:
        for m in RATIO_FIELD_RE.finditer(text):  # 비율 필드: 1.852 → 185.2%, 표시용 반올림 185% 도 허용
            pct = abs(float(m.group(2))) * 100
            nums.update(canon(f"{pct:.{digits}f}") for digits in (4, 1, 0))
    return nums


def number_tokens(text: str) -> Iterable[re.Match]:
    """한글에 붙은 금액도 검사하되 법령의 '제13조'는 금액으로 보지 않는다."""
    article_spans = [m.span() for m in ARTICLE_RE.finditer(text)]
    for match in NUMBER_WITH_UNIT_RE.finditer(text):
        if (MONEY_UNIT_RE.fullmatch(match.group(2))
                and not any(start <= match.start() < end for start, end in article_spans)):
            yield match
    # 일수와 비율 등 기존 검사의 토큰 경계 및 숫자 처리 규칙은 유지한다.
    yield from NON_MONEY_WITH_UNIT_RE.finditer(text)


def money_key(number: str, unit: str) -> tuple[str, str]:
    """쉼표/공백과 억·억원 표기만 정규화한다. 환산과 반올림은 하지 않는다."""
    unit = re.sub(r"\s+", "", unit)
    value = canon(number)
    if number.startswith("-") and value != "0":
        value = "-" + value
    return value, unit if unit.endswith("원") else unit + "원"


def known_money(texts: Iterable[str]) -> set[tuple[str, str]]:
    amounts: set[tuple[str, str]] = set()
    for text in texts:
        amounts.update(money_key(m.group(1), m.group(2)) for m in number_tokens(text)
                       if MONEY_UNIT_RE.fullmatch(m.group(2)))
        amounts.update(money_key(m.group(2), MONEY_FIELDS[m.group(1)])
                       for m in MONEY_FIELD_RE.finditer(text))
    return amounts


def normalize_article(a: str) -> str:
    return re.sub(r"\s+", "", a)


def paragraph_number(mark: str) -> int | None:
    """원문자 항 표시(①, ⑱, ㉑ …)를 숫자로. 원문자가 아니면 None."""
    code = ord(mark)
    for start, first in ((0x2460, 1), (0x3251, 21), (0x32B1, 36)):
        if start <= code < start + {0x2460: 20, 0x3251: 15, 0x32B1: 15}[start]:
            return first + code - start
    return None


def cited_paragraph(match: re.Match) -> tuple[str, int]:
    """ARTICLE_PARAGRAPH_RE 매치 → (정규화한 조, 항 번호)."""
    article, written, bare, mark = match.groups()
    return normalize_article(article), int(written or bare) if (written or bare) else paragraph_number(mark)


def known_paragraphs(evidence: list[Evidence], allowed_texts: Iterable[str]) -> tuple[set[tuple[str, int]], set[str]]:
    """근거에 실제로 있는 (조, 항)과, 항 정보가 하나라도 있는 조.

    조문 근거는 본문의 항 줄과 생략한 항 번호(meta.omitted_paragraphs)가 그 조의 항이다.
    검색 근거는 meta.paragraph 하나다. 인용 문구(label)나 질문에 적힌 "제13조①"도 인정한다.
    """
    pairs: set[tuple[str, int]] = set()
    for e in evidence:
        article = normalize_article(str(e.meta.get("article_no") or ""))
        if article:
            marks = [*PARAGRAPH_LINE_RE.findall(e.text), *(e.meta.get("omitted_paragraphs") or []),
                     *([e.meta["paragraph"]] if isinstance(e.meta.get("paragraph"), str) else [])]
            pairs |= {(article, n) for m in marks if isinstance(m, str) and len(m) == 1
                      and (n := paragraph_number(m)) is not None}
        for text in (e.label, e.text):
            pairs |= {cited_paragraph(m) for m in ARTICLE_PARAGRAPH_RE.finditer(text)}
    for text in allowed_texts:
        pairs |= {cited_paragraph(m) for m in ARTICLE_PARAGRAPH_RE.finditer(text)}
    return pairs, {article for article, _ in pairs}


def cited_ids(text: str) -> list[str]:
    ids: list[str] = []
    for group in CITE_RE.findall(text):
        for eid in split_ids(group):
            if eid not in ids:
                ids.append(eid)
    return ids


def check(text: str, evidence: list[Evidence], allowed_texts: Iterable[str] = ()) -> VerificationReport:
    """allowed_texts: 사용자 질문, 하위 질문, 가정처럼 숫자가 나와도 되는 다른 출처."""
    ids = {e.id for e in evidence if e.citable}     # 참고용 근거(조문 전문)는 인용하면 안 된다
    sources = [f"{e.label}\n{e.text}" for e in evidence]   # 숫자·조문은 참고용 근거에 있어도 근거 있음
    allowed = list(allowed_texts)
    numbers = known_numbers(sources + allowed)
    percentages = known_percentages(sources)
    amounts = known_money(sources + allowed)
    articles = {normalize_article(a) for s in sources + allowed for a in ARTICLE_RE.findall(s)}

    body = CITE_RE.sub("", text)
    ungrounded_numbers: set[str] = set()
    for match in number_tokens(body):
        number, unit = match.groups()
        grounded = (money_key(number, unit) in amounts if MONEY_UNIT_RE.fullmatch(unit)
                    else canon(number) in numbers
                    or (unit in ("%", "퍼센트") and canon(number) in percentages))
        if not grounded:
            ungrounded_numbers.add(match.group(0))
    ungrounded_articles = {a for a in ARTICLE_RE.findall(body) if normalize_article(a) not in articles}
    # 조는 근거에 있어도 항이 다르면 근거 없음이다 ("제13조 제18항"). 항 정보가 없는 조는 항으로 판단하지 않는다.
    # Spring 검증기가 verification 필드를 고정하므로 새 필드 없이 ungrounded_articles에 함께 담는다 (#160)
    paragraphs, judged = known_paragraphs(evidence, allowed)
    for match in ARTICLE_PARAGRAPH_RE.finditer(body):
        article, number = cited_paragraph(match)
        if article in judged and article in articles and (article, number) not in paragraphs:
            ungrounded_articles.add(match.group(0))
    return VerificationReport(
        unknown_citations=[i for i in cited_ids(text) if i not in ids],
        ungrounded_numbers=sorted(ungrounded_numbers),
        ungrounded_articles=sorted(ungrounded_articles),
    )


def feedback(report: VerificationReport) -> str:
    """재시도할 때 모델에게 줄 오류 설명."""
    lines = ["직전 답변에 근거 없는 내용이 있다. 아래를 고쳐서 다시 써라. 근거에 없는 값은 쓰지 말고 '확인 필요'라고 적어라."]
    if report.unknown_citations:
        lines.append(f"- 근거 목록에 없거나 인용할 수 없는(citable=\"no\") id: {', '.join(report.unknown_citations)}")
    if report.ungrounded_numbers:
        lines.append(f"- 근거·질문에 없는 숫자 또는 금액 단위: {', '.join(report.ungrounded_numbers)}")
        lines.append("  금액은 근거의 값과 단위를 그대로 써라. 원을 만원·억원·조원으로 환산하거나 반올림하지 마라.")
    if report.ungrounded_articles:
        lines.append(f"- 근거에 없는 조문·항 번호: {', '.join(report.ungrounded_articles)}")
        lines.append("  항 번호는 근거 조문에 실제로 있는 항만 써라. 다른 조의 항을 붙이지 마라.")
    return "\n".join(lines)


def mask(text: str, report: VerificationReport) -> str:
    """재시도 후에도 남은 근거 없는 값을 가린다. 틀린 법 정보를 보여 주는 것보다 낫다."""
    bad_ids = set(report.unknown_citations)

    def drop_bad(m: re.Match) -> str:
        keep = [i for i in split_ids(m.group(1)) if i not in bad_ids]
        return f"[{', '.join(keep)}]" if keep else ""

    text = CITE_RE.sub(drop_bad, text)
    # check()와 같은 규칙으로 토큰을 다시 찾아 그 위치만 가린다. 문자열 치환은 근거 있는 '60일' 안의
    # '0일'까지 바꿔 '6[확인 필요]'처럼 맞는 값을 깨뜨린다.
    numbers, articles = set(report.ungrounded_numbers), set(report.ungrounded_articles)
    spans = sorted({m.span() for m in number_tokens(text) if m.group(0) in numbers}
                   | {m.span() for m in ARTICLE_RE.finditer(text) if m.group(0) in articles}
                   | {m.span() for m in ARTICLE_PARAGRAPH_RE.finditer(text) if m.group(0) in articles})
    out, pos = [], 0
    for start, end in spans:
        if start < pos:          # 겹치는 토큰은 앞에서 이미 가렸다
            continue
        out += [text[pos:start], MASK]
        pos = end
    return "".join(out) + text[pos:]


# 금액 앞의 쉼표 숫자. "7,050,030,985,05원"처럼 모델이 쉼표 간격을 틀리면 값은 같아도 다른 금액으로 읽힌다
GROUPED_MONEY_RE = re.compile(rf"(?<![\d.,])(\d[\d,]*,\d+)(?=\s*{MONEY_UNIT})")
CORRECT_GROUPING_RE = re.compile(r"^\d{1,3}(?:,\d{3})+$")


def fix_digit_grouping(text: str) -> str:
    """금액 숫자의 쉼표를 세 자리 간격으로 바로잡는다. 숫자 자체는 바꾸지 않는다 (근거 검사는 쉼표를 무시하고 통과시킨다)."""
    def fix(m: re.Match) -> str:
        token = m.group(1)
        return token if CORRECT_GROUPING_RE.match(token) else f"{int(token.replace(',', '')):,}"
    return GROUPED_MONEY_RE.sub(fix, text)


def render_citations(text: str, evidence: list[Evidence]) -> tuple[str, list[Citation]]:
    """[E3] → [1]. 번호는 본문에 처음 나온 순서로 붙이고, 인용 목록을 만든다."""
    by_id = {e.id: e for e in evidence if e.citable}
    order: dict[str, int] = {}

    def renumber(m: re.Match) -> str:
        nums = []
        for eid in split_ids(m.group(1)):
            if eid not in by_id:
                continue
            order.setdefault(eid, len(order) + 1)
            nums.append(str(order[eid]))
        return f"[{', '.join(nums)}]" if nums else ""

    rendered = CITE_RE.sub(renumber, text)
    citations = [Citation(n=n, evidence_id=eid, label=by_id[eid].label, source_url=by_id[eid].source_url)
                 for eid, n in order.items()]
    return rendered, citations
