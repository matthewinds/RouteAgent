"""Behavioral regressions with protocol fixtures; never live transport evidence."""
import copy
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import socket
import pytest
from route_agent.models import Place,WeatherContext
from route_agent.ranking import rank_routes,exposure_reference,itinerary_label
from route_agent.transit import transit_via
from route_agent.verification import verify_route
from route_agent.tools import ToolFailure
from test_online import make_agent
from test_transit import transit_setup


def encode_mock(points):
    # Deliberately synthetic provider fixture; only production adapters decode it.
    out=[];last=[0,0]
    for lon,lat in points:
        for i,value in enumerate((round(lat*1e5),round(lon*1e5))):
            delta=value-last[i];last[i]=value
            number=~(delta<<1) if delta<0 else delta<<1
            while number>=32:
                out.append(chr((32|(number&31))+63));number>>=5
            out.append(chr(number+63))
    return ''.join(out)


@pytest.fixture
def mixed_provider(transit_setup,monkeypatch):
    provider,a,b,request,payload,seen=transit_setup
    def response(*args,**kwargs):
        params=kwargs['params'];seen.append(kwargs)
        origin=list(map(float,params['start'].split(',')))[::-1]
        dest=list(map(float,params['end'].split(',')))[::-1]
        depart=datetime.strptime(params['date']+' '+params['time'],'%m-%d-%Y %H:%M:%S').replace(tzinfo=ZoneInfo('Asia/Singapore'))
        def millis(seconds):return int((depart+timedelta(seconds=seconds)).timestamp()*1000)
        points=[[origin[i]+(dest[i]-origin[i])*fraction for i in (0,1)] for fraction in (0,.1,.4,.8,1)]
        legs=[]
        for index,(mode,start,end,meters) in enumerate((('WALK',20,50,100),('BUS',90,190,1000),('SUBWAY',230,430,2000),('WALK',430,460,100))):
            def endpoint(point):return {'name':'MOCK stop','lat':point[1],'lon':point[0]}
            legs.append({'mode':mode,'startTime':millis(start),'endTime':millis(end),'distance':meters,
                'from':endpoint(points[index]),'to':endpoint(points[index+1]),'routeShortName':'MOCK 123' if mode=='BUS' else 'MOCK DT' if mode=='SUBWAY' else None,
                'legGeometry':{'points':encode_mock(points[index:index+2])}})
        return {'plan':{'itineraries':[{'startTime':millis(20),'endTime':millis(460),'fare':'2.50','legs':legs}]}},'onemap'
    monkeypatch.setattr(provider,'request',response)
    return provider,a,b,request,seen


def test_automatic_plan_retains_walk_bus_rail_alternatives_and_selects_fastest(tmp_path,mixed_provider):
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path,mode=None,prefer_fastest=True,departure_time=request.departure_time)
    agent.providers.evidence.update(provider.evidence)
    agent.providers.transit_routes=provider.transit_routes
    agent.compare_modes();agent.choose_mode('walking',reason_codes=['time'])
    assert agent.request.mode is None
    output=agent.plan_candidates()
    assert not output['failures'] and {r.mode for r in agent.routes.values()}=={'walking','transit'}
    compared=agent.compare_routes()['candidates']
    assert compared[0]['mode']=='transit' and compared[0]['total_s']==460
    assert compared[0]['leg_modes']==['walking','bus','rail','walking']
    assert compared[0]['waiting_s']==100
    agent.finish_plan(compared[0]['id'],reason_codes=['time'])
    assert agent.result.status=='verified' and agent.request.mode is None
    assert 'MOCK 123' in agent.result.explanation and 'MOCK DT' in agent.result.explanation
    assert not agent.result.context['mode_selection']['provisional']


def test_switching_family_is_allowed_even_after_walking_verifies(tmp_path,mixed_provider):
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path,mode=None,departure_time=request.departure_time)
    agent.providers.transit_routes=provider.transit_routes
    agent.providers.evidence.update(provider.evidence)
    agent.plan_candidates(modes=['walking']);agent.validate_routes()
    assert all(r.feasibility=='verified' for r in agent.routes.values())
    agent.plan_candidates(modes=['transit']);agent.validate_routes()
    assert {r.mode for r in agent.routes.values()}=={'walking','transit'}
    assert not any(a['replan'] for a in agent.result.state.attempts)


def test_explicit_user_family_cannot_be_replaced(tmp_path):
    agent=make_agent(tmp_path,mode='walking')
    with pytest.raises(ToolFailure) as failure:agent.plan_candidates(modes=['transit'])
    assert failure.value.code=='immutable_request'


def test_missing_transit_does_not_mean_all_combinations_impossible(tmp_path):
    agent=make_agent(tmp_path,mode=None,max_duration_s=100,prefer_fastest=True)
    result=agent.plan_candidates()
    assert result['failures'][0]['mode']=='transit'
    agent.finish_plan(None)
    assert agent.result.status=='tool_error' and agent.result.context['error_code']=='incomplete_mode_search'
    assert '暂不能判断' in agent.result.message


def test_fastest_publication_requires_full_mode_comparison(tmp_path):
    agent=make_agent(tmp_path,mode=None,prefer_fastest=True)
    agent.plan_candidates(modes=['walking']);agent.validate_routes()
    with pytest.raises(ToolFailure) as error:agent.finish_plan(next(iter(agent.routes)))
    assert error.value.code=='missing_evidence'


def test_automatic_driving_needs_actual_vehicle_access(tmp_path):
    agent=make_agent(tmp_path,mode=None)
    agent.plan_candidates()
    assert all(r.mode!='driving' for r in agent.routes.values())
    request=agent.request.model_copy(update={'mode':'driving'})
    from route_agent.routing import assemble
    route=assemble(agent.providers.routes(agent.providers.a,agent.providers.b,'driving'),request,agent.providers.a,agent.providers.b)
    verify_route(route,agent.request,agent.providers.evidence)
    assert route.feasibility=='violated' and any(c.constraint=='car_access' and c.status=='fail' for c in route.checks)


def test_transit_business_stop_queries_onward_at_arrival_plus_dwell(mixed_provider):
    provider,a,b,request,seen=mixed_provider
    poi=Place(id='postal',name='MOCK post office',lat=1.295,lon=103.855,category='amenity:post_office',source='MOCK')
    request=request.model_copy(update={'poi_required':True,'poi_category':poi.category,'stop_duration_s':300,'max_duration_s':1300})
    route=transit_via(provider,a,b,poi,request)[0]
    assert len(seen)==2 and seen[1]['params']['time']=='10:13:00'
    assert route.total_s==1240 and route.stop_s==300
    assert route.poi_arrival_time==request.departure_time+timedelta(seconds=460)
    assert route.poi_departure_time==request.departure_time+timedelta(seconds=760)
    assert route.fare_sgd is None  # Two queries do not prove integrated transfer fare.
    verify_route(route,request,provider.evidence)
    assert route.feasibility=='verified'
    assert all(c.status=='pass' for c in route.checks)
    assert exposure_reference(route)['waiting_s']==220


def test_full_stop_journey_can_fail_deadline_even_when_direct_trip_passes(mixed_provider):
    provider,a,b,request,seen=mixed_provider
    poi=Place(id='bakery',name='MOCK bakery',lat=1.295,lon=103.855,category='bakery',source='MOCK')
    request=request.model_copy(update={'poi_required':True,'poi_category':'bakery','stop_duration_s':300,'max_duration_s':1000})
    route=transit_via(provider,a,b,poi,request)[0]
    verify_route(route,request,provider.evidence)
    assert route.feasibility=='violated' and any(c.constraint=='duration' and c.status=='fail' for c in route.checks)


@pytest.mark.parametrize('corruption',['omit_wait','overlap','short_stop','off_poi'])
def test_timing_guard_rejects_fabricated_transfers_or_stops(mixed_provider,corruption):
    provider,a,b,request,seen=mixed_provider
    poi=Place(id='bakery',name='MOCK bakery',lat=1.295,lon=103.855,category='bakery',source='MOCK')
    request=request.model_copy(update={'poi_required':True,'poi_category':'bakery','stop_duration_s':300})
    route=transit_via(provider,a,b,poi,request)[0]
    if corruption=='omit_wait':route.provider_total_s-=100
    if corruption=='overlap':route.legs[2].departure_time=route.legs[1].arrival_time-timedelta(seconds=1)
    if corruption=='short_stop':route.stop_s=400
    if corruption=='off_poi':route.poi=poi.model_copy(update={'lon':103.9})
    verify_route(route,request,provider.evidence)
    assert route.feasibility=='violated'
    assert any(c.constraint=='transit_timing' and c.status=='fail' for c in route.checks)


def test_unconfirmed_stop_time_is_not_inferred_from_deadline(mixed_provider):
    provider,a,b,request,seen=mixed_provider
    with pytest.raises(ToolFailure) as error:transit_via(provider,a,b,a,request)
    assert error.value.code=='missing_information' and not seen


def test_rain_comparison_uses_walking_and_waiting_not_only_forecast(tmp_path,mixed_provider):
    provider,a,b,request,seen=mixed_provider
    transit=provider.transit_routes(a,b,request)[0]
    walking=transit.model_copy(deep=True,update={'id':'long-walk','mode':'walking','walking_s':600,'walking_m':2000,'provider_total_s':450,'total_s':450})
    for r in (transit,walking):r.weather=WeatherContext(status='available',rain_fraction=.8)
    req=request.model_copy(update={'mode':None,'prefer_avoid_rain':True})
    ranked,_=rank_routes([walking,transit],req)
    assert ranked[0].id==transit.id
    assert exposure_reference(transit)['waiting_s']==100


def test_transit_corridor_search_uses_service_vertices(mixed_provider,monkeypatch):
    provider,a,b,request,seen=mixed_provider
    route=provider.transit_routes(a,b,request)[0]
    captured=[]
    monkeypatch.setattr(provider,'_pois',lambda centers,*args:captured.extend(centers) or [])
    provider.pois_along_route(route,'bakery')
    actual=[tuple(p) for l in route.legs for p in l.geometry['coordinates']]
    assert captured and all(tuple(p) in actual for p in captured)


def test_real_foot_access_precedes_transit_and_preserves_total(mixed_provider,tmp_path):
    from route_agent.transit import transit_access
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path)
    provider.routes=agent.providers.routes
    provider.evidence.update(agent.providers.evidence)
    hub=Place(id='hub',name='MOCK MRT',lat=1.292,lon=103.852,category='transit_hub',source='OneMap nearby transport',evidence_ids=['onemap'])
    route=transit_access(provider,a,b,request,origin_hub=hub)[0]
    assert route.provider_total_s==1060 and len(seen)==1
    assert seen[0]['params']['time']=='10:10:00'
    verify_route(route,request,provider.evidence)
    assert route.feasibility=='verified'
    assert route.legs[0].source=='OSRM' and route.legs[0].arrival_time==request.departure_time+timedelta(seconds=600)
    assert route.origin==a and route.destination==b


def test_access_to_both_endpoints_keeps_scheduled_stop_index(mixed_provider,tmp_path):
    from route_agent.transit import transit_access
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path);provider.routes=agent.providers.routes;provider.evidence.update(agent.providers.evidence)
    hub=Place(id='hub',name='MOCK MRT',lat=1.292,lon=103.852,category='transit_hub',source='OneMap nearby transport',evidence_ids=['onemap'])
    exit_hub=hub.model_copy(update={'id':'exit-hub','lat':1.298,'lon':103.858})
    poi=Place(id='bakery',name='MOCK Bakery',lat=1.295,lon=103.855,category='bakery',source='MOCK')
    request=request.model_copy(update={'poi_required':True,'poi_category':'bakery','stop_duration_s':300})
    route=transit_access(provider,a,b,request,poi,hub,exit_hub)[0]
    assert route.total_s==2440 and route.poi_leg_index==4
    verify_route(route,request,provider.evidence)
    assert route.feasibility=='verified'


def test_hub_access_cannot_bypass_total_walking_limit(mixed_provider,tmp_path):
    from route_agent.transit import transit_access
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path);provider.routes=agent.providers.routes
    request=request.model_copy(update={'max_walking_m':500})
    with pytest.raises(ToolFailure) as error:transit_access(provider,a,b,request,origin_hub=b)
    assert error.value.code=='no_route' and not seen


def test_hub_discovery_validates_sourced_coordinates_and_bounds(mixed_provider,monkeypatch):
    provider,a,b,request,seen=mixed_provider
    payload=[{'id':'MOCK Rail','name':'MOCK MRT','lat':1.292,'lon':103.852,'road':'MOCK Road'}]
    monkeypatch.setattr(provider,'request',lambda *a,**kw:(payload,'onemap'))
    hubs=provider.transit_hubs(a,'rail',2000,3)
    assert hubs[0].category=='transit_hub' and hubs[0].source=='OneMap nearby transport'
    payload[0]['lon']=180
    with pytest.raises(ToolFailure) as error:provider.transit_hubs(a,'rail',2000,3)
    assert error.value.code=='invalid_response'


def test_documented_no_route_404_is_distinct_from_broken_endpoint(mixed_provider,monkeypatch):
    import httpx
    from route_agent.providers import Providers
    provider,a,b,request,seen=mixed_provider
    # Restore the real shared response handler; transport remains mocked.
    provider.request=Providers.request.__get__(provider)
    monkeypatch.setattr(provider,'send_request',lambda *a,**kw:httpx.Response(404,json={'error':'No route found between the specified locations'},request=httpx.Request('GET','https://example.test/route')))
    with pytest.raises(ToolFailure) as error:provider.transit_routes(a,b,request)
    assert error.value.code=='no_route'
    monkeypatch.setattr(provider,'send_request',lambda *a,**kw:httpx.Response(404,text='Not Found',request=httpx.Request('GET','https://example.test/route')))
    with pytest.raises(ToolFailure) as error:provider.transit_routes(a,b,request)
    assert error.value.code=='provider_error'


def test_source_stop_anchor_allows_geometry_offsets_without_fake_connector(mixed_provider):
    provider,a,b,request,seen=mixed_provider
    route=provider.transit_routes(a,b,request)[0]
    route.legs[1].geometry['coordinates'][0][0]+=.00004
    verify_route(route,request,provider.evidence)
    assert next(c for c in route.checks if c.constraint=='continuity').status=='pass'
    route.legs[1].origin=route.legs[1].origin.model_copy(update={'lon':103.9,'id':'unrelated'})
    verify_route(route,request,provider.evidence)
    assert next(c for c in route.checks if c.constraint=='continuity').status=='unknown'


@pytest.mark.parametrize('seconds',[30,50])
def test_omitted_transfer_uses_real_foot_path_or_rejects_missed_boarding(mixed_provider,tmp_path,monkeypatch,seconds):
    from route_agent.models import RouteLeg
    provider,a,b,request,seen=mixed_provider
    fake=make_agent(tmp_path).providers
    provider.evidence.update(fake.evidence)
    original=provider.request
    def response(*args,**kwargs):
        payload,ev=original(*args,**kwargs)
        leg=payload['plan']['itineraries'][0]['legs'][2]
        leg['from']['lat']+=.0005
        leg['legGeometry']['points']=encode_mock([[leg['from']['lon'],leg['from']['lat']],[leg['to']['lon'],leg['to']['lat']]])
        return payload,ev
    monkeypatch.setattr(provider,'request',response)
    def foot(a,b,*args):
        return [RouteLeg(id='mock-connector',mode='walking',origin=a,destination=b,source='OSRM',
            distance_m=400,provider_duration_s=seconds,evidence_ids=['osrm'],geometry={'type':'LineString','coordinates':[[a.lon,a.lat],[b.lon,b.lat]]})]
    monkeypatch.setattr(provider,'routes',foot)
    if seconds>40:
        with pytest.raises(ToolFailure) as error:provider.transit_routes(a,b,request)
        assert error.value.code=='invalid_response'
    else:
        route=provider.transit_routes(a,b,request)[0]
        assert [l.mode for l in route.legs]==['walking','bus','walking','rail','walking']
        assert route.total_s==460 and route.walking_m==600 and route.walking_s==90
        assert 'osrm' in route.evidence_ids and exposure_reference(route)['waiting_s']==70
        verify_route(route,request,provider.evidence)
        assert route.feasibility=='verified'


def test_one_invalid_itinerary_does_not_erase_other_provider_options(transit_setup):
    provider,a,b,request,payload,seen=transit_setup
    bad=copy.deepcopy(payload['plan']['itineraries'][0]);bad['fare']='NaN'
    payload['plan']['itineraries'].append(bad)
    routes=provider.transit_routes(a,b,request)
    assert len(routes)==1 and routes[0].source_warnings


def test_fastest_preference_rejects_slower_choice_after_valid_comparison(tmp_path,mixed_provider):
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path,mode=None,prefer_fastest=True,departure_time=request.departure_time)
    agent.providers.transit_routes=provider.transit_routes;agent.providers.evidence.update(provider.evidence)
    agent.plan_candidates();agent.compare_routes()
    walking=next(r for r in agent.routes.values() if r.mode=='walking')
    with pytest.raises(ToolFailure) as error:agent.finish_plan(walking.id)
    assert error.value.code=='suboptimal_selection'


def test_replanning_one_family_reuses_other_complete_candidates(tmp_path,mixed_provider):
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path,mode=None,departure_time=request.departure_time)
    agent.plan_candidates(modes=['walking']);agent.validate_routes()
    agent.providers.transit_routes=provider.transit_routes;agent.providers.evidence.update(provider.evidence)
    output=agent.plan_candidates()  # Default comparison must not treat old walking success as a failure.
    assert not output['failures'] and {r.mode for r in agent.routes.values()}=={'walking','transit'}
    assert not agent.result.context['candidate_mode_comparison']['failures']


def test_valid_onemap_walking_segment_can_join_a_scheduled_business_trip(transit_setup):
    provider,a,b,request,payload,seen=transit_setup
    payload['plan']['itineraries'][0]['legs'][1]['mode']='WALK'
    with pytest.raises(ToolFailure) as failure:provider.transit_routes(a,b,request)
    assert failure.value.code=='no_route'
    journey=provider.transit_journeys(a,b,request)[0]
    assert journey.mode=='walking' and journey.total_s==900
    assert all(l.mode=='walking' and l.source=='OneMap transit' for l in journey.legs)
    payload['plan']['itineraries'][0]['legs'][1].pop('legGeometry')
    with pytest.raises(ToolFailure) as failure:provider.transit_journeys(a,b,request)
    assert failure.value.code=='invalid_response'  # Malformed data never becomes foot access.


def test_failed_later_departure_does_not_erase_completed_stop_journey(mixed_provider,monkeypatch):
    provider,a,b,request,seen=mixed_provider
    poi=Place(id='poi',name='MOCK Cafe',lat=1.295,lon=103.855,category='cafe',source='MOCK')
    request=request.model_copy(update={'poi_required':True,'stop_duration_s':300})
    real=provider.transit_journeys
    calls=[]
    def journeys(a,b,req):
        calls.append(req.departure_time)
        if len(calls)==3:raise ToolFailure('MOCK invalid onward geometry','invalid_response')
        routes=real(a,b,req)
        return routes*2 if len(calls)==1 else routes
    monkeypatch.setattr(provider,'transit_journeys',journeys)
    routes=transit_via(provider,a,b,poi,request)
    assert routes and len(calls)==3
    verify_route(routes[0],request,provider.evidence)
    assert routes[0].feasibility=='verified' and routes[0].stop_s==300


@pytest.mark.parametrize('failure_code',['invalid_response','budget_exhausted'])
def test_failed_combination_keeps_prior_success_and_records_source_reason(tmp_path,mixed_provider,failure_code):
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path,mode=None,departure_time=request.departure_time,poi_required=True,poi_category='cafe',stop_duration_s=300)
    agent.providers.evidence.update(provider.evidence)
    pois=[Place(id=f'poi{i}',name='MOCK Cafe',lat=1.295+i*.001,lon=103.855,category='cafe',source='MOCK') for i in range(3)]
    agent.places.update({p.id:p for p in pois})
    calls=[]
    def service(a,b,req):
        calls.append(b.id)
        if b.id==pois[1].id:raise ToolFailure('MOCK source failure',failure_code)
        return provider.transit_journeys(a,b,req)
    agent.providers.transit_routes=service
    output=agent.plan_candidates(poi_ids=[p.id for p in pois],modes=['transit'])
    assert output['route_ids'] and any(r.poi.id==pois[0].id for r in agent.routes.values())
    assert any(r.poi.id==pois[2].id for r in agent.routes.values())==(failure_code=='invalid_response')
    assert agent.result.state.attempts[0]['failures'][0]['error']==failure_code
    assert agent.result.context['candidate_mode_comparison']['partial_failures']
    agent.validate_routes(output['route_ids'])
    assert all(r.feasibility=='verified' for r in agent.routes.values())


def test_empty_failed_family_can_replan_after_other_family_succeeds(tmp_path,mixed_provider):
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path,mode=None,departure_time=request.departure_time,poi_required=True,poi_category='cafe',stop_duration_s=300)
    agent.providers.evidence.update(provider.evidence)
    pois=[Place(id=f'poi{i}',name='MOCK Cafe',lat=1.295+i*.001,lon=103.855,category='cafe',source='MOCK') for i in range(2)]
    agent.places.update({p.id:p for p in pois})
    agent.providers.transit_routes=lambda *args:(_ for _ in ()).throw(ToolFailure('MOCK bad geometry','invalid_response'))
    initial=agent.plan_candidates(poi_ids=[pois[0].id])
    agent.validate_routes(initial['route_ids'])
    agent.providers.transit_routes=provider.transit_journeys
    output=agent.plan_candidates(poi_ids=[pois[1].id],modes=['transit'])
    assert output['route_ids'] and agent.result.state.attempts[-1]['replan']
    assert not agent.result.context['candidate_mode_comparison']['failures']


def test_checking_successful_family_allows_improved_poi_search_independently(tmp_path,mixed_provider):
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path,mode=None,departure_time=request.departure_time,poi_required=True,poi_category='cafe',stop_duration_s=300)
    agent.providers.evidence.update(provider.evidence);agent.providers.transit_routes=provider.transit_journeys
    pois=[Place(id=f'poi{i}',name='MOCK Cafe',lat=1.295+i*.001,lon=103.855,category='cafe',source='MOCK') for i in range(2)]
    agent.places.update({p.id:p for p in pois})
    first=agent.plan_candidates(poi_ids=[pois[0].id],modes=['transit'])
    agent.plan_candidates(poi_ids=[pois[0].id],modes=['walking'])
    agent.validate_routes(first['route_ids'])
    assert agent.result.state.attempts[0]['checked'] and not agent.result.state.attempts[1]['checked']
    assert agent.plan_candidates(poi_ids=[pois[1].id],modes=['transit'])['route_ids']


def test_checked_feasible_route_does_not_block_broader_poi_search(tmp_path):
    agent=make_agent(tmp_path,poi_required=True,poi_category='cafe',stop_duration_s=300)
    poi=Place(id='poi',name='MOCK Cafe',lat=1.295,lon=103.855,category='cafe',source='MOCK')
    agent.places[poi.id]=poi
    agent.providers.pois=lambda *args:[poi]
    agent.plan_candidates(poi_ids=[poi.id]);agent.validate_routes()
    assert agent.search_pois(near='origin',limit=30)['places']


def test_business_search_reserves_budget_and_keeps_completed_options(mixed_provider,monkeypatch):
    provider,a,b,request,seen=mixed_provider
    poi=Place(id='poi',name='MOCK Cafe',lat=1.295,lon=103.855,category='cafe',source='MOCK')
    request=request.model_copy(update={'poi_required':True,'stop_duration_s':300})
    real=provider.transit_journeys
    def journeys(*args):
        routes=real(*args)
        if len(seen)==2:provider.budget.http_calls=provider.settings.max_http_calls-8
        return routes*2 if len(seen)==1 else routes
    monkeypatch.setattr(provider,'transit_journeys',journeys)
    routes=transit_via(provider,a,b,poi,request)
    assert routes and len(seen)==2 and routes[0].source_warnings


def test_poi_corridors_include_transit_before_any_mode_comparison(tmp_path,mixed_provider):
    provider,a,b,request,seen=mixed_provider
    agent=make_agent(tmp_path,mode=None,departure_time=request.departure_time,poi_required=True,poi_category='cafe',stop_duration_s=300)
    agent.providers.evidence.update(provider.evidence);agent.providers.transit_routes=provider.transit_routes
    families=[]
    def along(reference,*args):
        families.append(reference.mode)
        return [Place(id=reference.mode+str(i),name='MOCK Cafe',lat=1.295,lon=103.855,category='cafe',source='MOCK') for i in range(10)]
    agent.providers.pois_along_route=along
    output=agent.search_pois()
    assert families==['walking','transit'] and agent.request.mode is None
    assert len(output['places'])==10 and {p['id'].startswith('transit') for p in output['places']}=={True,False}
