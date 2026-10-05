"""General fuzzy-search protocol; fixture coordinates are not live map claims."""
import copy
import json
import pytest
from route_agent.coordinator import tool_schemas
from route_agent.models import Place
from route_agent.tools import ToolFailure
from test_online import make_agent

@pytest.mark.parametrize('description,alias',[
    ('某个科技公司的办公室','Example Research Tower'),
    ('岛上那个主题乐园','Example Theme Park'),
    ('中文大学名称','Example Technical University'),
    ('拼错的酒店名称','Example Heritage Hotel'),
])
def test_research_review_shortlist_and_confirm_work_for_any_place(tmp_path,description,alias):
    agent=make_agent(tmp_path);agent.request.origin=description
    target=Place(id='target',name=alias+', Singapore',lat=1.29,lon=103.85,source='MOCK MAP')
    surrounding=target.model_copy(update={'id':'surrounding','name':'Nearby shop, Singapore'})
    def geocode(query):return [target,surrounding] if query==alias else []
    agent.providers.geocode=geocode
    output=agent.execute('resolve_place',json.dumps({'role':'origin','search_queries':[alias]}),'research')
    assert len(output['candidates'])==2 and not agent.done and 'origin' not in agent.roles
    assert agent.request.origin==description
    with pytest.raises(ToolFailure) as error:agent.ask_user(['origin'])
    assert error.value.code=='candidates_available'
    agent.execute('resolve_place',json.dumps({'role':'origin','candidate_ids':['target'],'match_kind':'description_candidate'}),'present')
    assert set(agent.result.questions)=={'origin_place_id'}
    assert [p['id'] for p in agent.choices['origin_place_id']]==['target']
    saved=copy.deepcopy(agent.place_searches)
    resumed=make_agent(tmp_path);resumed.request.origin=description;resumed.providers.geocode=geocode
    resumed.clarifications={'origin_place_id':'target','place_searches':saved}
    reply=resumed.execute('resolve_place',json.dumps({'role':'origin'}),'resume')
    assert reply['user_confirmed'] and resumed.roles['origin'].id=='target' and not resumed.done


def test_selected_candidate_replays_its_own_source_after_later_empty_search(tmp_path):
    agent=make_agent(tmp_path);agent.request.destination='描述中的酒店'
    agent.providers.geocode=lambda q:[Place(id='hotel',name='Example Hotel',lat=1.29,lon=103.85,source='MOCK MAP')] if q=='Example Hotel' else []
    agent.resolve_place('destination',['Example Hotel'],present_candidates=False)
    agent.resolve_place('destination',['Empty query'],present_candidates=False)
    agent.resolve_place('destination',candidate_ids=['hotel'],match_kind='name_alias')
    saved=copy.deepcopy(agent.place_searches)
    agent.done=False;agent.clarifications={'destination_place_id':'hotel','place_searches':saved}
    output=agent.resolve_place('destination')
    assert output['resolved']['id']=='hotel' and output['user_confirmed']


def test_unknown_or_other_role_candidates_cannot_be_presented(tmp_path):
    agent=make_agent(tmp_path)
    agent.resolve_place('origin',['City Hall'],present_candidates=False)
    for ids in (['generated-coordinates'],[agent.providers.a.id]):
        with pytest.raises(ToolFailure) as error:agent.resolve_place('destination',candidate_ids=ids)
        assert error.value.code=='unknown_id'


def test_configured_web_search_must_be_attempted_before_requesting_a_new_description(tmp_path,monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY','mock-key')
    agent=make_agent(tmp_path);agent.providers.geocode=lambda q:[]
    agent.resolve_place('destination',['Example Landmark'],present_candidates=False)
    with pytest.raises(ToolFailure) as error:agent.ask_user(['destination'])
    assert error.value.code=='retrieval_incomplete' and not agent.done
    agent.providers.web_location_sources=lambda q:{'documents':[],'failures':[],
        'search_configured':True,'next_step':'No reliable source found'}
    agent.resolve_place('destination',['Example Landmark Singapore'],source='web',present_candidates=False)
    agent.ask_user(['destination'])
    assert '尚未取得可靠地址候选' in agent.result.questions['destination']


def test_tool_schema_requires_both_endpoint_fields_and_defaults_to_research():
    specs={t['function']['name']:t['function']['parameters'] for t in tool_schemas()}
    assert set(specs['set_request']['properties']['fields']['required'])=={'origin','destination'}
    assert specs['resolve_place']['properties']['present_candidates']['default'] is False


def test_irrelevant_candidates_can_be_rejected_without_forcing_a_wrong_choice(tmp_path,monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    agent=make_agent(tmp_path)
    agent.providers.geocode=lambda q:[Place(id='wrong',name='Different Nearby Attraction',lat=1.29,lon=103.85,source='MOCK MAP')]
    agent.resolve_place('destination',['Search hypothesis'],present_candidates=False)
    agent.resolve_place('destination',reject_candidate_ids=['wrong'])
    agent.ask_user(['destination'])
    assert agent.done and not agent.result.recommended and '尚未取得可靠地址候选' in agent.result.questions['destination']


def test_named_stop_language_hypothesis_matches_real_name_and_keeps_original_request(tmp_path):
    agent=make_agent(tmp_path,poi_name='示例品牌',poi_category='amenity:fast_food',poi_required=True,stop_duration_s=1200)
    shop=Place(id='chain',name='Example Chain',lat=1.295,lon=103.855,category='amenity:fast_food',source='MOCK OSM',evidence_ids=['geo'])
    received=[]
    def pois(*args):received.append(args[-1]);return [shop]
    agent.providers.pois=pois
    agent.search_pois('origin',search_names=['Example Chain'])
    assert received==[['示例品牌','Example Chain']]
    agent.plan_candidates(poi_ids=['chain']);agent.validate_routes()
    route=next(iter(agent.routes.values()))
    stop=next(c for c in route.checks if c.constraint=='required_poi')
    assert stop.status=='pass' and stop.required=='示例品牌'
    assert agent.request.poi_name=='示例品牌' and route.stop_s==1200
    wrong=shop.model_copy(update={'name':'Other Chain'})
    agent.places[wrong.id]=wrong
    with pytest.raises(ToolFailure,match='名称'):agent._plan_mode_candidates('transit',poi_ids=['chain'])


def test_real_source_multilingual_names_are_searchable_without_invented_translations(tmp_path,monkeypatch):
    from route_agent.config import Settings
    from route_agent.providers import Providers
    from route_agent.tools import Budget
    s=Settings(root=tmp_path);p=Providers(s,Budget(s))
    rows=[{'type':'node','id':1,'lat':1.29,'lon':103.85,'tags':{'amenity':'fast_food','name':'Example Chain','name:zh':'示例品牌'}},
        {'type':'node','id':2,'lat':1.29,'lon':103.85,'tags':{'amenity':'fast_food','name':'Other Chain','brand:wikidata':'Q123'}}]
    monkeypatch.setattr(p,'overpass',lambda *a:({'elements':rows},'osm-evidence'))
    places=p._pois([[103.85,1.29]],'amenity:fast_food',1000,10,'示例品牌')
    assert len(places)==1 and places[0].names==['Example Chain','示例品牌']
    assert places[0].evidence_ids==['osm-evidence']
    assert p._pois([[103.85,1.29]],'amenity:fast_food',1000,10,'Q123')==[]
