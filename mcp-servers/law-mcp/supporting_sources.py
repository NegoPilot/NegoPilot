"""Pinned official supporting sources, independent of the six-law refresh lifecycle."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'data' / 'official'

def load_sources():
    path=ROOT/'catalog.json'
    catalog=json.loads(path.read_text())
    docs={}
    for doc in catalog['documents']:
        for asset in doc['assets']:
            data=(ROOT/asset['path']).read_bytes()
            if hashlib.sha256(data).hexdigest()!=asset['sha256']:
                raise ValueError('Official source checksum mismatch: '+asset['path'])
        docs[doc['document_id']]=doc
    return docs

def available(doc, day):
    return (doc.get('effective_date') or doc.get('published_date')) <= day

def metadata(doc):
    return {k:v for k,v in doc.items() if k!='text'}

def chunks(text, limit=1200):
    # Keep layout/paragraphs; overlap protects context at long paragraph boundaries.
    for start in range(0,len(text),limit-200):
        yield start,text[start:start+limit]

def search_units(docs, law_keys, day):
    for doc in docs.values():
        if not available(doc,day) or not set(doc['parent_laws']).intersection(law_keys): continue
        for offset,text in chunks(doc['text']):
            yield {'document':doc,'offset':offset,'text':text}
