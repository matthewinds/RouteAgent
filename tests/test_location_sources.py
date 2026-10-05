"""Location retrieval uses sources and re-querying, not per-company fixes."""
import copy
import socket
import pytest
from route_agent.config import Settings
from route_agent.providers import Providers
from route_agent.models import Place
from route_agent.tools import Budget,ToolFailure
from test_online import make_agent


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket,"connect",lambda *a,**k:pytest.fail("No real network in source protocol tests"))


def registry_row(name="EXAMPLE COMPANY PTE. LTD.",country="SG"):
    address={"country":country,"addressLines":["17 Example Road","#02-01"],"postalCode":"123456"}
    return {"id":"A"*20,"attributes":{"entity":{"legalName":{"name":name},"status":"ACTIVE",
        "legalAddress":address,"headquartersAddress":copy.deepcopy(address)}}}


def provider_with_registry(tmp_path,monkeypatch,rows):
    settings=Settings(root=tmp_path); provider=Providers(settings,Budget(settings))
    seen=[]
    def request(*args,**kwargs):seen.append(kwargs);return {"data":rows},"registry"
    monkeypatch.setattr(provider,"request",request)
    queries=[]
    def geocode(query):
        queries.append(query)
        return [Place(id="pelias:real-address",name="17 Example Road",lat=1.29,lon=103.85,source="ORS Pelias",evidence_ids=["map"])]
    monkeypatch.setattr(provider,"geocode",geocode)
    return provider,seen,queries


def test_any_matching_company_address_is_mapped_with_two_sources_and_confirmation(tmp_path,monkeypatch):
    provider,seen,queries=provider_with_registry(tmp_path,monkeypatch,[registry_row()])
    places=provider.company_locations("Example Company")
    assert len(places)==1 and queries==["17 Example Road"]
    place=places[0]
    assert place.company_name=="EXAMPLE COMPANY PTE. LTD." and place.requires_confirmation
    assert "#02-01" in place.address and place.evidence_ids==["registry","map"]
    assert "不一定是你的实际办公地点" in place.access_note
    assert place.source_url.endswith("/lei-records/"+"A"*20)
    assert seen[0]["params"]["filter[entity.legalAddress.country]"]=="SG"


def test_other_companies_countries_and_inactive_records_are_not_locations(tmp_path,monkeypatch):
    inactive=registry_row();inactive["attributes"]["entity"]["status"]="INACTIVE"
    provider,_,queries=provider_with_registry(tmp_path,monkeypatch,[registry_row("UNRELATED PTE. LTD."),registry_row(country="US"),inactive])
    assert provider.company_locations("Example Company")==[] and not queries


def test_registry_address_without_real_map_match_cannot_create_coordinates(tmp_path,monkeypatch):
    provider,_,_=provider_with_registry(tmp_path,monkeypatch,[registry_row()])
    monkeypatch.setattr(provider,"geocode",lambda _:[])
    assert provider.company_locations("Example Company")==[]


def test_source_errors_are_not_reported_as_empty_results(tmp_path,monkeypatch):
    provider,_,_=provider_with_registry(tmp_path,monkeypatch,[])
    monkeypatch.setattr(provider,"request",lambda *a,**k:({"errors":[{"status":"400"}]},"bad"))
    with pytest.raises(ToolFailure) as error:provider.company_locations("Example Company")
    assert error.value.code=="invalid_response"


def test_empty_alias_search_keeps_agent_running_for_another_source(tmp_path):
    agent=make_agent(tmp_path)
    agent.request.destination="Example公司"
    agent.roles.pop("destination")
    agent.providers.geocode=lambda _:[]
    agent.providers.company_locations=lambda name:[Place(id="sourced-company",name="Example Company · 17 Example Road",
        lat=1.29,lon=103.85,source="GLEIF + ORS Pelias",requires_confirmation=True,location_kind="company_address")]
    empty=agent.resolve_place("destination",["Example Singapore office"])
    assert empty["resolved"] is None and not agent.done and not agent.result.questions
    agent.resolve_place("destination",["Example"],source="registry")
    assert agent.done and list(agent.result.questions)==["destination_place_id"]
    assert agent.request.destination=="Example公司" and len(agent.choices["destination_place_id"])==1


def test_saved_failed_search_does_not_override_new_queries_or_source(tmp_path):
    agent=make_agent(tmp_path)
    agent.request.destination="Example公司"
    agent.clarifications={"place_searches":{"destination":{"query":"Example公司","search_queries":["old bad alias"],"source":"map"}}}
    seen=[]
    agent.providers.company_locations=lambda name:seen.append(name) or []
    output=agent.resolve_place("destination",["Example"],source="registry")
    assert seen==["Example"] and output["source"]=="registry" and not agent.done


def test_confirmed_company_replays_source_and_query_without_reasking(tmp_path):
    agent=make_agent(tmp_path)
    agent.request.destination="Example公司"
    agent.roles.pop("destination")
    place=Place(id="company-source-id",name="Example Company · 17 Example Road",lat=1.29,lon=103.85,
        source="GLEIF + ORS Pelias",requires_confirmation=True,location_kind="company_address")
    seen=[]
    agent.providers.company_locations=lambda name:seen.append(name) or [place.model_copy(deep=True)]
    agent.resolve_place("destination",["Example"],source="registry")
    saved=copy.deepcopy(agent.place_searches)
    agent.done=False
    agent.clarifications={"destination_place_id":place.id,"place_searches":saved}
    output=agent.resolve_place("destination")
    assert seen==["Example"]*2 and output["user_confirmed"]
    assert not output["resolved"]["requires_confirmation"] and not agent.done
