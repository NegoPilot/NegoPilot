from negopilot_agent.generation.evidence import from_search_regulation, from_check_offer


def test_template_and_ocr_qualification_reaches_generated_evidence():
    result=from_search_regulation([{
        'citation':'표준서식', 'text':'계약서 내용', 'document_id':'linkage_templates',
        'type':'template', 'version_note':'표준서식과 다르다는 이유만으로 위법 아님',
        'text_completeness':'unreviewed_ocr', 'published_date':'2026-08-18',
        'source_url':'https://www.mss.go.kr/'}])[0]
    assert '위법 아님' in result['text']
    assert 'unreviewed_ocr' in result['text']
    assert result['meta']['document_type']=='template'


def test_size_uncertainty_and_official_source_are_preserved():
    result=from_check_offer({'company_size':{'supplier':{
        'status':'unknown','missing':['average_revenue_eok'],
        'source_url':'https://www.law.go.kr/'}},
        'applicability':'conditional_on_transaction_and_buyer_requirements'})
    size=next(e for e in result if e['kind']=='company_size')
    assert 'average_revenue_eok' in size['text']
    assert size['source_url']=='https://www.law.go.kr/'
    assert size['meta']['applicability']=='conditional_on_transaction_and_buyer_requirements'
