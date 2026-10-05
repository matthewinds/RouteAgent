"""Resolve sourced corporate addresses through the real map service.

GLEIF registry/headquarters addresses are candidates, not proof of a user's
workplace. No brand/address table or generated coordinates are used.
"""
import re
from .providers import stable,location_name_matches
from .tools import ToolFailure


def company_locations(provider,name):
    base=provider.settings.company_registry_base.rstrip("/")
    payload,ev=provider.request("GET",base+"/lei-records","GLEIF company addresses",
        params={"filter[entity.legalName]":name,"filter[entity.legalAddress.country]":"SG","page[size]":5},
        headers={"Accept":"application/vnd.api+json","User-Agent":"FYP-RouteAgent/0.2"},ttl=86400)
    if not isinstance(payload,dict) or not isinstance(payload.get("data"),list) or payload.get("errors"):
        raise ToolFailure("公司地址来源返回结构无效，未生成替代地址。","invalid_response")
    places=[]
    for row in payload["data"][:5]:
        if not isinstance(row,dict):
            raise ToolFailure("公司地址记录结构无效。","invalid_response")
        attrs=row.get("attributes",{}); entity=attrs.get("entity",{}) if isinstance(attrs,dict) else None
        if not isinstance(entity,dict) or not isinstance(entity.get("legalName",{}),dict):
            raise ToolFailure("公司地址记录结构无效。","invalid_response")
        legal_name=entity.get("legalName",{}).get("name","")
        lei=row.get("id","")
        if not isinstance(legal_name,str) or not isinstance(lei,str) or not re.fullmatch(r"[A-Z0-9]{20}",lei) or entity.get("status")!="ACTIVE":
            continue
        # A provider full-text hit alone is insufficient to associate a
        # different legal entity or parent with the user's brand.
        if not location_name_matches(name,legal_name):
            continue
        grouped={}
        for key,label in (("headquartersAddress","总部地址"),("legalAddress","登记地址")):
            address=entity.get(key) or {}
            if not isinstance(address,dict):
                raise ToolFailure("公司地址记录结构无效。","invalid_response")
            if address.get("country")!="SG":
                continue
            lines=address.get("addressLines")
            if not isinstance(lines,list) or not lines or any(not isinstance(x,str) for x in lines):
                continue
            street=next((x.strip() for x in lines if x.strip() and not x.strip().startswith("#")),None)
            if not street:
                continue
            full=", ".join([*lines,"SINGAPORE",str(address.get("postalCode") or "")]).strip(", ")
            marker=full.casefold()
            if marker in grouped:
                grouped[marker]["kind"]+="／"+label
            else:
                grouped[marker]={"street":street,"full":full,"kind":label}
        for entry in grouped.values():
            # Building/street queries omit unit/floor numbers; preserve the
            # complete registry address and disclose the mapped scope.
            for mapped in provider.geocode(entry["street"]):
                note="这是公司来源记录的"+entry["kind"]+"，不一定是你的实际办公地点；地图只定位楼宇／街道，未核实楼层和入口，请确认。"
                places.append(mapped.model_copy(update={
                    "id":"company:"+lei+":"+stable([mapped.id,entry["full"]])[:16],
                    "name":legal_name+" · "+entry["full"]+"（"+entry["kind"]+"）",
                    "company_name":legal_name,"address":entry["full"],
                    "source":"GLEIF + ORS Pelias","source_url":base+"/lei-records/"+lei,
                    "evidence_ids":list(dict.fromkeys([ev,*mapped.evidence_ids])),
                    "requires_confirmation":True,"location_kind":"company_address","access_note":note}))
    return list({p.id:p for p in places}.values())[:5]
