"""Mocked official fare protocols; no tests call real fare services."""
import copy
from datetime import timedelta
import json

import pytest

from route_agent.fares import (calculate_fare, ZERO_STATE, valid_transfer, rides_for,
                              rail_stations, transfer_rules)
from route_agent.models import Place, RouteLeg, RouteCandidate
from route_agent.providers import now_utc
from route_agent.tools import ToolFailure
from test_online import make_agent, deny_network


RULES = """The maximum duration between the first and last boarding of a bus or train
within a journey is two hours. A maximum of five transfers can be made within a journey.
45 minutes for transfers between train station and bus service, or between different bus services.
15 minutes for transfers between different train stations.
The current bus service must not be the same number as the preceding bus service.
No exit and re-entry at the same train station."""
RAIL = '<mrtTripFareCalc><mrtStop value="226_129">Telok Blangah (CC28)</mrtStop><mrtStop value="227_78">HarbourFront (NE1 / CC29)</mrtStop><mrtStop value="10_10">Clarke Quay (NE5)</mrtStop></mrtTripFareCalc>'
BUS = '<busTripFareCalc>' + ''.join(f'<bus id="{s}"><direction id="2"><busStop id="1611_16079_1">16079 - Origin</busStop><busStop id="1531_14169_2">14169 - Destination</busStop></direction></bus>' for s in ("30","30A","10")) + '</busTripFareCalc>'
OPERATOR = '<p>Sentosa Express</p><p>- Standard Fee per guest: $4</p><p>Once on the island, you can travel at no cost using our public transport system, which includes the Sentosa Express, Sentosa Bus and Beach Shuttle!</p>'


class FareProvider:
    def __init__(self):
        self.seen=[];self.fare_calls=[];self.fail_at=None;self.operator=OPERATOR
        self.response_override=None
    def request(self,method,url,source,**kwargs):
        self.seen.append((method,url,source,copy.deepcopy(kwargs)))
        if url.endswith('mrtTripIndex.xml'):return {'text':RAIL},'rail-index'
        if url.endswith('busTripIndex.xml'):return {'text':BUS},'bus-index'
        if 'ptc.gov.sg' in url:return {'text':RULES},'rules'
        if 'sentosa.com.sg' in url:return {'text':self.operator},'operator'
        self.fare_calls.append(copy.deepcopy(kwargs['data']))
        n=len(self.fare_calls)
        if self.fail_at==n:raise ToolFailure('MOCK service failure','network_error')
        if self.response_override is not None:return self.response_override,'fare'+str(n)
        return {'fare':'159' if n==1 else '9','distance':'530' if n==1 else '150',
                'tripInfo':'state'+str(n),'addTripInfo':'additional'+str(n)},'fare'+str(n)


def make_route():
    start=now_utc().replace(second=0,microsecond=0)
    def leg(mode,service,from_code,to_code,begin,end,n):
        a=Place(id='a'+str(n),name=from_code,stop_code=from_code,lat=1.29,lon=103.85,source='OneMap transit')
        b=Place(id='b'+str(n),name=to_code,stop_code=to_code,lat=1.30,lon=103.86,source='OneMap transit')
        return RouteLeg(id='leg'+str(n),mode=mode,service_name=service,origin=a,destination=b,
            source='OneMap transit',evidence_ids=['onemap'],distance_m=1000,provider_duration_s=end-begin,
            geometry={'type':'LineString','coordinates':[[a.lon,a.lat],[b.lon,b.lat]]},
            departure_time=start+timedelta(seconds=begin),arrival_time=start+timedelta(seconds=end))
    legs=[leg('bus','30','16079','14169',0,600,0),leg('rail','CC','CC28','CC29',2400,2500,1),
          leg('rail','SS','S1','S2',2800,3000,2)]
    return RouteCandidate(id='mock-transit',mode='transit',origin=legs[0].origin,destination=legs[-1].destination,
        legs=legs,geometry={'type':'MultiLineString','coordinates':[l.geometry['coordinates'] for l in legs]},
        distance_m=3000,provider_total_s=3000,total_s=3000,evidence_ids=['onemap'])


def test_complete_fare_uses_transfer_state_and_separate_operator_fee():
    provider=FareProvider();route=make_route()
    quote=calculate_fare(provider,route)
    assert quote['fare_sgd']==5.68 and quote['error'] is None
    assert provider.fare_calls[0]['tripInfo']==ZERO_STATE
    assert provider.fare_calls[1]['tripInfo']=='state1'
    assert provider.fare_calls[1]['addTripInfo']=='additional1'
    assert [p['journey_group'] for p in route.fare_components]==[1,1,2]
    assert [p['fare_sgd'] for p in route.fare_components]==[1.59,.09,4]
    assert {'rail-index','bus-index','rules','fare1','fare2','operator'}<=set(route.fare_evidence_ids)
    assert '成人' in route.fare_profile


@pytest.mark.parametrize('gap,reset',[(2700,False),(2701,True)])
def test_full_tap_gap_controls_reset_including_walk_wait_and_dwell(gap,reset):
    provider=FareProvider();route=make_route();route.legs=route.legs[:2]
    start=route.legs[0].arrival_time+timedelta(seconds=gap)
    route.legs[1].departure_time=start;route.legs[1].arrival_time=start+timedelta(seconds=100)
    calculate_fare(provider,route)
    assert (provider.fare_calls[1]['tripInfo']==ZERO_STATE)==reset
    assert route.fare_components[1]['journey_group']==(2 if reset else 1)


@pytest.mark.parametrize('elapsed,count,allowed',[(7200,5,True),(7201,1,False),(7000,6,False)])
def test_first_last_boarding_and_transfer_count_limits(elapsed,count,allowed):
    provider=FareProvider();rules,_=transfer_rules(provider);t=now_utc()
    previous={'kind':'bus','service':'30','arrival':t+timedelta(seconds=elapsed-60)}
    current={'kind':'mrt','departure':t+timedelta(seconds=elapsed)}
    assert valid_transfer(previous,current,t,count,rules)[0] is allowed


def test_same_or_subsidiary_bus_service_starts_a_new_fare_journey():
    route=make_route();route.legs=route.legs[:2]
    second=route.legs[0].model_copy(deep=True)
    second.id='second';second.service_name='30A'
    second.departure_time=route.legs[1].departure_time;second.arrival_time=route.legs[1].arrival_time
    route.legs[1]=second;provider=FareProvider();calculate_fare(provider,route)
    assert provider.fare_calls[1]['tripInfo']==ZERO_STATE
    assert '附属' in route.fare_components[1]['transfer_basis']


def test_paid_rail_interchange_merges_but_business_exit_does_not():
    import xml.etree.ElementTree as ET
    route=make_route();route.legs=route.legs[1:]
    second=route.legs[1];second.service_name='NE';second.origin.stop_code='NE1';second.destination.stop_code='NE5'
    stations=rail_stations(ET.fromstring(RAIL))
    rides=rides_for(route,stations,None)
    assert len(rides)==1 and rides[0]['leg_ids']==['leg1','leg2']
    route.poi_leg_index=0
    rides=rides_for(route,stations,None)
    assert len(rides)==2
    provider=FareProvider();calculate_fare(provider,route)
    assert provider.fare_calls[1]['tripInfo']==ZERO_STATE


def test_partial_fare_never_becomes_full_total():
    route=make_route();provider=FareProvider();provider.fail_at=2
    quote=calculate_fare(provider,route)
    assert quote['fare_sgd'] is None and quote['known_subtotal_sgd']==1.59
    assert quote['error']['code']=='network_error'
    assert route.fare_components[-1]['fare_sgd'] is None


def test_changed_operator_page_cannot_fall_back_to_hardcoded_price():
    route=make_route();provider=FareProvider();provider.operator='No price currently available'
    quote=calculate_fare(provider,route)
    assert quote['fare_sgd'] is None and quote['known_subtotal_sgd']==1.68
    assert quote['error']['code']=='fare_coverage'


@pytest.mark.parametrize('response',[{'fare':'','distance':'150'}, {'fare':'NaN','distance':'150'},
    {'fare':'-2','distance':'150'}, {'fare':'100','distance':'0'}, {'fare':'100','distance':'150'},
    {'fare':True,'distance':'150','tripInfo':'s','addTripInfo':'a'}])
def test_invalid_service_response_does_not_publish_amount(response):
    route=make_route();provider=FareProvider();provider.response_override=response
    quote=calculate_fare(provider,route)
    assert quote['fare_sgd'] is None and quote['known_subtotal_sgd'] is None
    assert quote['error']['code']=='invalid_response'


def test_unknown_station_is_not_matched_by_guessed_name():
    route=make_route();route.legs[1].origin.stop_code='UNKNOWN'
    provider=FareProvider();quote=calculate_fare(provider,route)
    assert quote['fare_sgd'] is None and quote['error']['code']=='fare_coverage'
    assert provider.fare_calls==[]


def test_station_aliases_come_from_catalogue_including_lrt_interchanges():
    import xml.etree.ElementTree as ET
    root=ET.fromstring('<mrtTripFareCalc><mrtStop value="99_77">Sengkang (NE16 / STC)</mrtStop></mrtTripFareCalc>')
    assert rail_stations(root)=={'NE16':'99','STC':'99'}


def test_island_ride_is_sourced_free_and_has_no_lta_transfer_dependency():
    route=make_route();route.legs=route.legs[-1:]
    route.legs[0].origin.stop_code='S2';route.legs[0].destination.stop_code='S3'
    provider=FareProvider();quote=calculate_fare(provider,route)
    assert quote['fare_sgd']==0 and quote['known_subtotal_sgd']==0
    assert len(provider.seen)==1 and provider.seen[0][2]=='Official operator tariff'


def test_unsupported_operator_direction_stays_unknown():
    route=make_route();route.legs=route.legs[-1:]
    route.legs[0].origin.stop_code='S2';route.legs[0].destination.stop_code='S1'
    quote=calculate_fare(FareProvider(),route)
    assert quote['fare_sgd'] is None and quote['known_subtotal_sgd'] is None


def test_agent_prices_known_routes_once_and_restores_deadline(tmp_path):
    agent=make_agent(tmp_path,mode='transit');route=make_route();agent.routes[route.id]=route
    provider=FareProvider();agent.providers.request=provider.request
    agent.providers.request_deadline=123456789.0
    result=agent.execute('get_transit_fares',json.dumps({'route_ids':[route.id]}),'fare-call')
    assert result['routes'][0]['fare_sgd']==5.68
    calls=len(provider.seen)
    agent.get_transit_fares([route.id])
    assert len(provider.seen)==calls and agent.providers.request_deadline==123456789.0
    assert agent.result.context['transit_fares'][0]['fare_sgd']==5.68
    output=agent.execute('get_transit_fares',json.dumps({'route_ids':['invented']}),'unknown-fare')
    assert output['error']=='unknown_id'


def test_publish_requests_missing_fare_lookup_but_does_not_invent_it(tmp_path):
    agent=make_agent(tmp_path,mode='transit');route=make_route();agent.routes[route.id]=route
    provider=FareProvider();agent.providers.request=provider.request
    output=agent.execute('finish_plan',json.dumps({'selected_id':route.id}),'premature-finish')
    assert output['error']=='missing_evidence' and 'get_transit_fares' in output['repair_hint']
    assert not provider.seen and route.fare_sgd is None


def test_expired_fare_evidence_cannot_remain_a_complete_cost_reference(tmp_path):
    from route_agent.verification import verify_route
    agent=make_agent(tmp_path,mode='transit',prefer_low_cost=True)
    route=make_route();provider=FareProvider();calculate_fare(provider,route)
    for ident in route.fare_evidence_ids:
        agent.providers.add(ident,'LTA fare calculator')
    agent.providers.evidence['fare1'].valid_until=now_utc()-timedelta(seconds=1)
    verify_route(route,agent.request,agent.providers.evidence)
    assert route.fare_sgd is None and route.fare_known_subtotal_sgd is None
    assert '过期' in route.fare_basis
    assert next(c for c in route.checks if c.constraint=='cost').status=='unknown'
