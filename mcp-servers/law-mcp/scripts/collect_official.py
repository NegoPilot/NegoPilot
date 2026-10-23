"""Collect pinned P0 primary sources. Requires pdftotext; never substitutes summaries.
Run from any directory: python scripts/collect_official.py
Changing a source/version requires review before committing the generated catalog.
"""
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'official'
LAW = 'https://www.law.go.kr/LSW/'

class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.skip += 1
        if tag in ('p','div','br','tr','h2'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script','style'): self.skip -= 1
        if tag in ('p','div','tr'): self.parts.append('\n')
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)

def plain(s):
    p=Text(); p.feed(s)
    return '\n'.join(x.strip() for x in ''.join(p.parts).splitlines() if x.strip())

def fetch(url, filename):
    req=urllib.request.Request(url, headers={'User-Agent':'NegoPilot-legal-source-collector/1.0'})
    with urllib.request.urlopen(req, timeout=45) as r: data=r.read()
    if not data: raise ValueError('Empty source: '+url)
    digest=hashlib.sha256(data).hexdigest()
    name=Path(filename)
    filename=name.stem+'_'+digest[:16]+name.suffix
    path=ROOT/'raw'/filename; path.write_bytes(data)
    return data, {'url':url,'path':'raw/'+filename,'sha256':hashlib.sha256(data).hexdigest()}

def ocr(path):
    cache=path.with_suffix(path.suffix+'.ocr.txt')
    if cache.exists(): return cache.read_text()
    result=subprocess.run(['swift',str(Path(__file__).with_name('ocr_source.swift')),str(path)],capture_output=True,check=True)
    text=result.stdout.decode()
    if len(text.strip())<10: raise ValueError('Empty OCR '+str(path))
    cache.write_text(text)
    return text

def pdf(url, key):
    data,asset=fetch(url,key+'.pdf')
    if not data.startswith(b'%PDF'): raise ValueError('Not a PDF: '+url)
    result=subprocess.run(['pdftotext','-layout',str(ROOT/asset['path']),'-'],capture_output=True,check=True)
    text=result.stdout.decode()
    if len(text.strip())<100:
        text='[OCR transcription: verify against original PDF]\n'+ocr(ROOT/asset['path'])
    return text,[asset]

def main():
    (ROOT/'raw').mkdir(parents=True,exist_ok=True)
    docs=[]; collected=datetime.now(timezone.utc).isoformat()
    def add(key,title,kind,effective,url,text,assets,parent,**extra):
        if len(text.strip())<100: raise ValueError('Missing body: '+key)
        docs.append(dict(document_id=key,title=title,kind=kind,effective_date=effective,
            source_url=url,collected_at=collected,parent_laws=parent,assets=assets,text=text,**extra))
    # Parent snapshot establishes annex-to-version relationship, including transitional clauses.
    parent='https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=281337&efYd=20260102'
    body,asset=fetch(LAW+'lsInfoR.do?lsiSeq=281337&efYd=20260102&chrClsCd=010202','sme_decree_281337.html')
    for n,seq,title in [(1,'159973059','주된 업종별 평균매출액등의 중소기업 규모 기준'),
                        (2,'159973067','관계기업의 평균매출액등의 산정기준'),
                        (3,'159973075','주된 업종별 평균매출액등의 소기업 규모 기준')]:
        if seq not in body.decode(): raise ValueError('Annex parent mismatch')
        url=LAW+f'flDownload.do?gubun=&flSeq={seq}&bylClsCd=110201'
        text,assets=pdf(url,f'sme_annex_{n}')
        add(f'sme_annex_{n}',f'중소기업기본법 시행령 별표 {n}: {title}','annex','2026-01-02',parent,text,[asset,*assets],['sme_decree','sme_act'],version_note='2026-01-02 시행령에 부속된 별표. 별표 자체 개정일과 구분.')
    rules=[
        ('manufacturing_scope','제조위탁의 대상이 되는 물품의 범위 고시','2100000030279'),
        ('linkage_guideline','하도급대금 연동제 운영지침','2100000253994'),
        ('late_interest','선급금 등 지연지급 시의 지연이율 고시','2100000171352'),
        ('bill_discount','어음에 의한 하도급대금 지급시의 할인율 고시','2100000030280'),
        ('price_reduction','부당한 하도급대금 결정 및 감액행위에 대한 심사지침','2100000216232'),
        ('restricted_information','하도급법상 요구가 금지되는 경영상 정보의 종류 고시','2100000139049')]
    for key,title,seq in rules:
        url=LAW+f'admRulInfoP.do?admRulSeq={seq}&chrClsCd=010201'
        raw,asset=fetch(url.replace('InfoP','InfoR'),key+'.html'); s=raw.decode()
        if title not in s: raise ValueError('Wrong rule '+key)
        # Retain body, not navigation/hidden fields. Embedded images are original legal tables.
        s=s[s.index('<div class="viewwrap"'):]
        text=plain(s); match=re.search(r'\[시행\s*(\d{4})\.\s*(\d+)\.\s*(\d+)\.',text)
        if not match: raise ValueError('Missing effective date '+key)
        effective='%s-%02d-%02d'%(match[1],int(match[2]),int(match[3]))
        assets=[asset]; images=[]
        for img in dict.fromkeys(re.findall(r'<img[^>]+src="([^"]*flDownload[^" ]*)"',s)):
            imgurl='https://www.law.go.kr'+html.unescape(img)
            seqimg=re.search(r'flSeq=(\d+)',imgurl)[1]
            _,a=fetch(imgurl,key+'_'+seqimg+'.image');assets.append(a);images.append(a['path'])
            text+='\n[Image table OCR; verify original: '+a['path']+']\n'+ocr(ROOT/a['path'])
        add(key,title,'administrative_rule',effective,url,text,assets,['subcontract_act','subcontract_decree'],
            image_tables=images,text_completeness='includes_unreviewed_image_ocr' if images else 'complete',
            version_note='고정 판본. 상위법 개정과 적용 시점을 함께 검토; 지침을 법률보다 우선하지 않음.')
    url='https://www.mss.go.kr/site/smba/ex/bbs/View.do?bcIdx=1070526&cbIdx=81&parentSeq=1070526'
    raw,parent_asset=fetch(url,'linkage_templates_notice.html'); s=raw.decode()
    assets=[parent_asset]; text=''
    for ext in ('pdf','hwpx'):
        path=html.unescape(re.search(r'href="([^\"]*Download.do[^\"]*\.'+ext+r')"',s)[1])
        download='https://www.mss.go.kr'+path
        if ext=='pdf': text,aa=pdf(download,'linkage_templates');assets.extend(aa)
        else: _,a=fetch(download,'linkage_templates.hwpx');assets.append(a)
    add('linkage_templates','표준 연동계약서 및 표준 미연동계약서(2026)','template',None,url,text,assets,['subcontract_act','winwin_act'],published_date='2026-08-18',version_note='게시일은 법적 시행일이 아님. 표준서식과 다르다는 이유만으로 위법 아님.')
    url='https://www.smes.go.kr/pis/front/board/11/view.do?boardSeq=357'
    raw,a=fetch(url,'energy_guide_notice.html')
    path=html.unescape(re.search(r'href="([^\"]*files/download.do[^\"]*)"',raw.decode())[1])
    text,assets=pdf('https://www.smes.go.kr'+path,'energy_linkage_guide')
    add('energy_linkage_guide','에너지비용(경비) 하도급(납품)대금 연동 기업 실무 가이드','guidance',None,url,text,[a,*assets],['subcontract_act','winwin_act'],published_date='2026-06-30',version_note='해설자료. 하도급법 2026-08-11 / 상생협력법 2026-12-03 적용 구분 필요.')
    for doc in docs:
        for asset in list(doc['assets']):
            ocr_path=ROOT/(asset['path']+'.ocr.txt')
            if ocr_path.exists():
                doc['assets'].append({'url':asset['url'],'path':str(ocr_path.relative_to(ROOT)),
                    'sha256':hashlib.sha256(ocr_path.read_bytes()).hexdigest(),'derived':'unreviewed OCR'})
        if '[OCR transcription:' in doc['text']:
            doc['text_completeness']='unreviewed_ocr'
    out={'schema_version':1,'collected_at':collected,'documents':docs}
    target=ROOT/'catalog.json'; tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');tmp.replace(target)
    for d in docs: print(d['document_id'],len(d['text']),len(d['assets']),d['effective_date'])

if __name__=='__main__': main()
