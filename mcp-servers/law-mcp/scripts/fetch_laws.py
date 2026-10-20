"""법령 원문을 legalize-kr 저장소에서 내려받아 data/raw 아래 파일로 저장한다.

한 번만 실행하면 되고, MCP 서버는 실행 중에 네트워크를 쓰지 않는다.
법령마다 최근 개정본 여러 개를 함께 받아 두는 이유는, 공포됐지만 아직
시행되지 않은 개정본이 있을 수 있어서 "오늘 시행 중인 판"을 고르기 위해서다.

사용법:
    uv run python scripts/fetch_laws.py            # 기본 법령 목록
    uv run python scripts/fetch_laws.py --versions 6
    GITHUB_TOKEN=$(gh auth token) uv run python scripts/fetch_laws.py   # API 제한 회피
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "legalize-kr/legalize-kr"
API = f"https://api.github.com/repos/{REPO}/commits"
RAW = f"https://raw.githubusercontent.com/{REPO}"

# law_key: 서버에서 쓰는 짧은 이름, path: legalize-kr 안의 경로
TARGETS = [
    {"law_key": "subcontract_act", "path": "kr/하도급거래공정화에관한법률/법률.md"},
    {"law_key": "subcontract_decree", "path": "kr/하도급거래공정화에관한법률/시행령.md"},
    {"law_key": "winwin_act", "path": "kr/대ㆍ중소기업상생협력촉진에관한법률/법률.md"},
    {"law_key": "winwin_decree", "path": "kr/대ㆍ중소기업상생협력촉진에관한법률/시행령.md"},
    {"law_key": "sme_act", "path": "kr/중소기업기본법/법률.md"},
    {"law_key": "sme_decree", "path": "kr/중소기업기본법/시행령.md"},
]

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"


def http_get(url: str) -> bytes:
    headers = {"User-Agent": "law-mcp-fetcher"}
    # 비인증 GitHub API는 시간당 60회 제한이 있어 토큰이 있으면 사용한다
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def list_commits(path: str, count: int) -> list[str]:
    query = urllib.parse.urlencode({"path": path, "per_page": count})
    commits = json.loads(http_get(f"{API}?{query}"))
    return [c["sha"] for c in commits]


def frontmatter_value(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}:\s*'?([^'\n]*)'?\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def fetch_target(target: dict, versions: int) -> list[dict]:
    out_dir = RAW_DIR / target["law_key"]
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    seen_effective = set()
    for sha in list_commits(target["path"], versions):
        url = f"{RAW}/{sha}/{urllib.parse.quote(target['path'])}"
        text = http_get(url).decode("utf-8")
        effective = frontmatter_value(text, "시행일자")
        if not effective or effective in seen_effective:
            continue
        seen_effective.add(effective)
        file = out_dir / f"{effective}_{sha[:10]}.md"
        file.write_text(text, encoding="utf-8")
        saved.append({"file": str(file.relative_to(ROOT)), "effective": effective,
                      "promulgated": frontmatter_value(text, "공포일자"), "commit": sha})
        print(f"  saved {file.relative_to(ROOT)}")
    return saved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--versions", type=int, default=6,
                        help="법령마다 받아 올 최근 개정본 수")
    args = parser.parse_args()

    manifest = {"source": f"https://github.com/{REPO}", "laws": {}}
    for target in TARGETS:
        print(f"{target['law_key']}: {target['path']}")
        try:
            manifest["laws"][target["law_key"]] = {
                "path": target["path"],
                "versions": fetch_target(target, args.versions),
            }
        except Exception as exc:  # 한 법령이 실패해도 나머지는 계속 받는다
            print(f"  failed: {exc}", file=sys.stderr)

    (RAW_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: {(RAW_DIR / 'manifest.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
