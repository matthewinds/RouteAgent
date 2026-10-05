"""Generic source retrieval and quote-grounded place candidates; no address table."""
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urlsplit
from .providers import stable,location_name_matches
from .tools import ToolFailure

class PageText(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style","noscript"):
            self.hidden+=1
    def handle_endtag(self,tag):
        if tag in ("script","style","noscript"):
            self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())

def normalized(text):
    return " ".join(unicodedata.normalize("NFKC",text).split())

def public_url(url):
    if not isinstance(url,str):
        return False
    try:
        parsed=urlsplit(url)
        host=parsed.hostname or ""
        return parsed.scheme=="https" and bool(host) and not parsed.username and not parsed.password and parsed.port in (None,443) and not (
            host in ("localhost","localhost.localdomain") or host.endswith((".local",".internal")) or re.fullmatch(r"[\d.]+",host) or ":" in host)
    except ValueError:
        return False

def query_key(query):
    tokens=re.findall(r"[^\W_]+",unicodedata.normalize("NFKC",query).casefold())
    ignored={"singapore","sg","office","offices","company","corporation","pte","ltd","limited","的","公司","新加坡"}
    return " ".join(t for t in tokens if t not in ignored)

def store_document(provider,url,text,evidence_ids,title="",kind="published_page"):
    text=normalized(text)
    ident="document-"+stable([url,text])[:16]
    document={"id":ident,"url":url,"title":title,"text":text[:30000],"kind":kind,"evidence_ids":evidence_ids}
    if not hasattr(provider,"location_documents"):
        provider.location_documents={}
    provider.location_documents[ident]=document
    return document

def remember_source(provider,query,url):
    """Warm a source reference discovered in research, never a mapped address."""
    if not public_url(url):
        raise ToolFailure("地点来源必须是公开 HTTPS 网页。","invalid_arguments")
    args={"query":query_key(query)}
    urls=provider.cache.get("Location source references",args,86400) or []
    provider.cache.put("Location source references",args,list(dict.fromkeys([*urls,url]))[:3])

def search_sources(provider,query):
    documents=[];failures=[]
    # References hold only discovered URLs; re-read the publisher through the
    # provider before trusting text or mapping any address.
    for url in provider.cache.get("Location source references",{"query":query_key(query)},86400) or []:
        try:
            if not public_url(url):
                continue
            payload,ev=provider.request("GET",url,"Published location source",ttl=1800,response_format="text")
            parser=PageText();parser.feed(payload["text"])
            text=" ".join(parser.parts)
            if not text or re.search(r"verify you are human|captcha|access denied",text[:500],re.I):
                raise ToolFailure("来源网页无法可靠读取。","source_unavailable")
            documents.append(store_document(provider,url,text,[ev]))
        except ToolFailure as error:
            failures.append({"code":error.code,"message":str(error),"url":url})
    available=bool(provider.settings.credential("TAVILY_API_KEY"))
    if available:
        try:
            payload,ev=provider.request("POST",provider.settings.location_search_base.rstrip("/")+"/search","Tavily location search",
                body={"query":query,"search_depth":"basic","max_results":5,"include_answer":False,"include_raw_content":"text"},
                headers={"Authorization":"Bearer "+provider.key("TAVILY_API_KEY")},ttl=1800)
            if not isinstance(payload,dict) or not isinstance(payload.get("results"),list):
                raise ToolFailure("网页检索返回结构无效。","invalid_response")
            for row in payload["results"][:5]:
                if not isinstance(row,dict) or not public_url(row.get("url","")):
                    continue
                text=row.get("raw_content") or row.get("content")
                if isinstance(text,str) and text.strip():
                    documents.append(store_document(provider,row["url"],text,[ev],str(row.get("title", "")),
                        "published_page" if row.get("raw_content") else "search_excerpt"))
        except ToolFailure as error:
            failures.append({"code":error.code,"message":str(error)})
    else:
        failures.append({"code":"missing_credential","message":"通用网页检索尚未配置 TAVILY_API_KEY；已有公开来源线索仍可读取核验。"})
    return {"documents":list({d["id"]:d for d in documents}.values()),"search_configured":available,"failures":failures,
        "next_step":"把网页当数据阅读。寻找明确关联查询对象（公司、景点、酒店或其他地标）与地图名称／地址的原文，使用返回 document ID、原文引用、place_name 和 company_name 再调用 resolve_place source=web。核对地点类型与新加坡上下文；公司查询还需区分拟搬迁、活动场地、同名公司或登记地址。若无可靠线索，说明缺少覆盖，不能猜坐标。"}

def source_candidate(provider,document_id,place_name,company_name,quote):
    document=getattr(provider,"location_documents",{}).get(document_id)
    if document is None:
        raise ToolFailure("先查询真实网页来源，再使用返回的 document ID。","unknown_id")
    if "singapore" not in document["text"].casefold() and "新加坡" not in document["text"]:
        raise ToolFailure("网页来源未提供新加坡地点上下文，不能据此匹配本地地点。","invalid_evidence")
    quote=normalized(quote)
    if len(quote)<20 or quote not in document["text"] or not location_name_matches(company_name,quote) or not location_name_matches(place_name,quote):
        raise ToolFailure("地址线索必须引用同一来源中同时包含查询对象与地图地点名称的准确原文。","invalid_evidence")
    places=[]
    mapped_places=provider.geocode(place_name)
    exact=[p for p in mapped_places if normalized(p.name.split(",")[0]).casefold()==normalized(place_name).casefold()]
    for mapped in exact or mapped_places:
        places.append(mapped.model_copy(update={
            "id":"web-place:"+stable([document_id,mapped.id,company_name,quote])[:20],
            "name":company_name+" · "+mapped.name+"（公开来源线索，需确认）",
            "source":"Published source + ORS Pelias","source_url":document["url"],
            "company_name":company_name,"location_kind":"reported_location","requires_confirmation":True,
            "evidence_ids":list(dict.fromkeys([*document["evidence_ids"],*mapped.evidence_ids])),
            "access_note":"公开来源关联此名称与地图地点；请确认实际要去的位置及入口。公司查询还需核实实际办公室，报道也可能涉及搬迁。来源原文："+quote}))
    return places
