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

CITE_RE = re.compile(r"\[\s*(E\d+(?:\s*,\s*E\d+)*)\s*\]")
# 긴 단위를 먼저 잡아 '3억원'을 '3억' 또는 '3원'으로 잘라 비교하지 않는다.
MONEY_UNIT = r"(?:(?:[십백천]\s*)?[경조억만](?:\s*원)?|[십백천]\s*원|원)"
NUMBER_WITH_UNIT_RE = re.compile(
    rf"(?<![A-Za-z0-9_.])(-?\d[\d,]*(?:\.\d+)?)\s*({MONEY_UNIT}|일|%|퍼센트|개|곳|ppm|배)")
MONEY_UNIT_RE = re.compile(MONEY_UNIT)
NON_MONEY_WITH_UNIT_RE = re.compile(r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)\s*(일|%|퍼센트|개|곳|ppm|배)")
NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
ARTICLE_RE = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?")
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
RATIO_FIELDS = ("debt_ratio", "operating_margin", "on_time_rate", "raw_material_ratio")
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


def cited_ids(text: str) -> list[str]:
    ids: list[str] = []
    for group in CITE_RE.findall(text):
        for eid in re.split(r"\s*,\s*", group):
            if eid not in ids:
                ids.append(eid)
    return ids


def check(text: str, evidence: list[Evidence], allowed_texts: Iterable[str] = ()) -> VerificationReport:
    """allowed_texts: 사용자 질문, 하위 질문, 가정처럼 숫자가 나와도 되는 다른 출처."""
    ids = {e.id for e in evidence if e.citable}     # 참고용 근거(조문 전문)는 인용하면 안 된다
    sources = [f"{e.label}\n{e.text}" for e in evidence]   # 숫자·조문은 참고용 근거에 있어도 근거 있음
    allowed = list(allowed_texts)
    numbers = known_numbers(sources + allowed)
    amounts = known_money(sources + allowed)
    articles = {normalize_article(a) for s in sources + allowed for a in ARTICLE_RE.findall(s)}

    body = CITE_RE.sub("", text)
    ungrounded_numbers: set[str] = set()
    for match in number_tokens(body):
        number, unit = match.groups()
        grounded = (money_key(number, unit) in amounts if MONEY_UNIT_RE.fullmatch(unit)
                    else canon(number) in numbers)
        if not grounded:
            ungrounded_numbers.add(match.group(0))
    return VerificationReport(
        unknown_citations=[i for i in cited_ids(text) if i not in ids],
        ungrounded_numbers=sorted(ungrounded_numbers),
        ungrounded_articles=sorted({a for a in ARTICLE_RE.findall(body) if normalize_article(a) not in articles}),
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
        lines.append(f"- 근거에 없는 조문 번호: {', '.join(report.ungrounded_articles)}")
    return "\n".join(lines)


def mask(text: str, report: VerificationReport) -> str:
    """재시도 후에도 남은 근거 없는 값을 가린다. 틀린 법 정보를 보여 주는 것보다 낫다."""
    bad_ids = set(report.unknown_citations)

    def drop_bad(m: re.Match) -> str:
        keep = [i for i in re.split(r"\s*,\s*", m.group(1)) if i not in bad_ids]
        return f"[{', '.join(keep)}]" if keep else ""

    text = CITE_RE.sub(drop_bad, text)
    for token in sorted(report.ungrounded_numbers + report.ungrounded_articles, key=len, reverse=True):
        text = text.replace(token, MASK)
    return text


def render_citations(text: str, evidence: list[Evidence]) -> tuple[str, list[Citation]]:
    """[E3] → [1]. 번호는 본문에 처음 나온 순서로 붙이고, 인용 목록을 만든다."""
    by_id = {e.id: e for e in evidence if e.citable}
    order: dict[str, int] = {}

    def renumber(m: re.Match) -> str:
        nums = []
        for eid in re.split(r"\s*,\s*", m.group(1)):
            if eid not in by_id:
                continue
            order.setdefault(eid, len(order) + 1)
            nums.append(str(order[eid]))
        return f"[{', '.join(nums)}]" if nums else ""

    rendered = CITE_RE.sub(renumber, text)
    citations = [Citation(n=n, evidence_id=eid, label=by_id[eid].label, source_url=by_id[eid].source_url)
                 for eid, n in order.items()]
    return rendered, citations
