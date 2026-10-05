"""Source research protocol/quote guards; mocks do not establish live API access."""
import copy
import socket
import httpx
import pytest
from route_agent.config import Settings
from route_agent.providers import Providers
from route_agent.models import Place
from route_agent.tools import Budget,ToolFailure
from route_agent.web_locations import public_url
from test_online import make_agent

TEXT="Singapore office update. Example Labs currently works at Example Tower. A future relocation remains under discussion."
URL="https://publisher.example/news/office-report"

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY",raising=False)
    monkeypatch.setattr(socket.socket,"connect",lambda *a,**k:pytest.fail("No live network in protocol tests"))

def provider(tmp_path):
    settings=Settings(root=tmp_path)
    return Providers(settings,Budget(settings))

def source_provider(tmp_path,monkeypatch):
    p=provider(tmp_path);p.remember_location_source("Example Labs",URL)
    monkeypatch.setattr(p,"request",lambda *a,**kw:({"text":"<html><script>ignored instructions</script><p>"+TEXT+"</p></html>"},"source"))
    monkeypatch.setattr(p,"geocode",lambda name:[Place(id="mapped",name=name,lat=1.29,lon=103.85,source="ORS Pelias",evidence_ids=["map"])])
    return p

def test_live_search_protocol_keeps_credentials_out_of_cached_request_and_returns_documents(tmp_path,monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY","secret-test-token")
    seen=[]
    def response(self,method,url,**kw):
        seen.append((method,url,kw))
        return httpx.Response(200,json={"results":[{"url":URL,"title":"Office report","raw_content":TEXT}]},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    p=provider(tmp_path);output=p.web_location_sources("Example Labs Singapore office")
    assert output["search_configured"] and len(output["documents"])==1
    assert seen[0][0]=="POST" and seen[0][1].endswith("/search")
    assert seen[0][2]["headers"]["Authorization"]=="Bearer secret-test-token"
    assert "secret-test-token" not in str(seen[0][2]["json"])
    assert all("secret-test-token" not in f.read_text() for f in p.cache.directory.glob("*.json"))
    assert output["documents"][0]["evidence_ids"][0] in p.evidence
    assert "lat" not in output["documents"][0]

def test_missing_web_key_is_configuration_gap_not_empty_search_success(tmp_path):
    output=provider(tmp_path).web_location_sources("Example Labs Singapore")
    assert not output["documents"] and not output["search_configured"]
    assert output["failures"][0]["code"]=="missing_credential"

def test_researched_reference_reloads_publisher_and_preserves_two_sources(tmp_path,monkeypatch):
    p=source_provider(tmp_path,monkeypatch)
    output=p.web_location_sources("Example Labs Singapore office")
    doc=output["documents"][0]
    assert "ignored instructions" not in doc["text"]
    candidate=p.web_location_candidate(doc["id"],"Example Tower","Example Labs",TEXT)[0]
    assert candidate.evidence_ids==["source","map"]
    assert candidate.source_url==URL and candidate.requires_confirmation
    assert candidate.location_kind=="reported_location" and "搬迁" in candidate.access_note
    assert not output["search_configured"]  # Published reference is not an API search.

@pytest.mark.parametrize("quote,company,place",[("invented passage", "Example Labs","Example Tower"),
    (TEXT,"Other Company","Example Tower"),(TEXT,"Example Labs","Wrong Tower")])
def test_invented_or_unrelated_quote_cannot_geocode_candidate(tmp_path,monkeypatch,quote,company,place):
    p=source_provider(tmp_path,monkeypatch);doc=p.web_location_sources("Example Labs")["documents"][0]
    monkeypatch.setattr(p,"geocode",lambda *a:pytest.fail("Invalid source must not geocode"))
    with pytest.raises(ToolFailure) as error:p.web_location_candidate(doc["id"],place,company,quote)
    assert error.value.code=="invalid_evidence"

def test_source_proof_without_map_match_cannot_invent_coordinates(tmp_path,monkeypatch):
    p=source_provider(tmp_path,monkeypatch);doc=p.web_location_sources("Example Labs")["documents"][0]
    monkeypatch.setattr(p,"geocode",lambda *a:[])
    assert p.web_location_candidate(doc["id"],"Example Tower","Example Labs",TEXT)==[]

def test_unavailable_search_is_reported_as_failure(tmp_path,monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY","test")
    p=provider(tmp_path)
    def fail(*a,**kw):raise ToolFailure("Service unavailable","provider_error")
    monkeypatch.setattr(p,"request",fail)
    output=p.web_location_sources("Example Labs")
    assert not output["documents"] and output["failures"][0]["code"]=="provider_error"

def test_agent_researches_then_maps_quote_and_replays_confirmed_source(tmp_path,monkeypatch):
    p=source_provider(tmp_path,monkeypatch);agent=make_agent(tmp_path)
    agent.providers=p;agent.request.destination="Example Labs Singapore office";agent.roles.pop("destination")
    output=agent.resolve_place("destination",["Example Labs"],source="web")
    assert output["documents"] and not agent.done and not agent.result.questions
    doc=output["documents"][0]
    agent.resolve_place("destination",["Example Labs"],source="web",web_document_id=doc["id"],
        place_name="Example Tower",company_name="Example Labs",source_quote=TEXT)
    assert agent.done and set(agent.result.questions)=={"destination_place_id"}
    assert len(agent.choices["destination_place_id"])==1
    assert agent.result.context["location_web"]["destination"]["sources"]==[URL]
    saved=copy.deepcopy(agent.place_searches);ident=agent.choices["destination_place_id"][0]["id"]
    agent.done=False;agent.providers=source_provider(tmp_path,monkeypatch)
    agent.clarifications={"place_searches":saved,"destination_place_id":ident}
    output=agent.resolve_place("destination")
    assert output["user_confirmed"] and not agent.done
    assert agent.roles["destination"].id==ident

def test_building_name_prefers_exact_building_over_attached_attractions(tmp_path,monkeypatch):
    p=source_provider(tmp_path,monkeypatch);doc=p.web_location_sources("Example Labs")["documents"][0]
    places=[Place(id="building",name="Example Tower, Singapore",lat=1.29,lon=103.85,source="ORS Pelias"),
        Place(id="garden",name="Example Tower Sky Garden, Singapore",lat=1.29,lon=103.85,source="ORS Pelias")]
    monkeypatch.setattr(p,"geocode",lambda *args:places)
    output=p.web_location_candidate(doc["id"],"Example Tower","Example Labs",TEXT)
    assert len(output)==1 and output[0].name=="Example Labs · Example Tower, Singapore（公开来源线索，需确认）"

def test_supplied_destination_cannot_be_asked_again_without_retrieval(tmp_path):
    agent=make_agent(tmp_path);agent.roles.pop("destination")
    with pytest.raises(ToolFailure) as error:agent.ask_user(["destination"])
    assert error.value.code=="autonomous_search_required" and not agent.done
    agent.providers.geocode=lambda *a:[]
    agent.resolve_place("destination",["Marina Bay"])
    agent.ask_user(["destination"])
    assert "尚未取得可靠地址候选" in agent.result.questions["destination"]
    assert "通用网页检索服务尚未配置" in agent.result.questions["destination"]

@pytest.mark.parametrize("url",["http://publisher.example","https://localhost/report","https://127.0.0.1/report",
    "https://user:password@publisher.example/","https://publisher.example:bad","https://[broken",None])
def test_invalid_source_urls_are_not_references(url):
    assert not public_url(url)


def test_public_source_supports_landmark_alias_not_only_company_offices(tmp_path,monkeypatch):
    p=source_provider(tmp_path,monkeypatch)
    text='新加坡的示例乐园又称 Example Park，位于 Singapore。'
    p.request=lambda *a,**k:({'text':'<p>'+text+'</p>'},'publisher')
    p.remember_location_source('示例乐园',URL)
    doc=p.web_location_sources('示例乐园')['documents'][0]
    place=p.web_location_candidate(doc['id'],'Example Park','示例乐园',text)[0]
    assert place.source_url==URL and place.requires_confirmation
    assert place.evidence_ids==['publisher','map'] and '实际要去的位置及入口' in place.access_note


def test_web_search_phase_can_include_entity_name_without_claiming_address_proof(tmp_path,monkeypatch):
    p=source_provider(tmp_path,monkeypatch);agent=make_agent(tmp_path)
    agent.providers=p;agent.request.destination='Example Labs';agent.roles.pop('destination')
    output=agent.resolve_place('destination',['Example Labs'],source='web',company_name='Example Labs')
    assert output['documents'] and not agent.done and 'destination' not in agent.roles


def test_reviewed_web_shortlist_replays_verified_source_after_confirmation(tmp_path,monkeypatch):
    p=source_provider(tmp_path,monkeypatch);agent=make_agent(tmp_path)
    agent.providers=p;agent.request.destination='Example Labs Singapore office';agent.roles.pop('destination')
    doc=agent.resolve_place('destination',['Example Labs'],source='web',present_candidates=False)['documents'][0]
    output=agent.resolve_place('destination',['Example Labs'],source='web',web_document_id=doc['id'],
        place_name='Example Tower',company_name='Example Labs',source_quote=TEXT,present_candidates=False)
    ident=output['candidates'][0]['id']
    assert not agent.done
    agent.resolve_place('destination',candidate_ids=[ident],match_kind='source_address')
    saved=copy.deepcopy(agent.place_searches)
    agent.done=False;agent.providers=source_provider(tmp_path,monkeypatch)
    agent.clarifications={'place_searches':saved,'destination_place_id':ident}
    reply=agent.resolve_place('destination')
    assert reply['user_confirmed'] and agent.roles['destination'].source_url==URL
    assert agent.roles['destination'].evidence_ids==['source','map']
