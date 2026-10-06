"""LTA DataMall v6.10 catalogue and bounded, source-backed dataset retrieval.

Signed download URLs stay in memory and never enter results or the disk cache.
The agent chooses datasets/parameters; statistical data never becomes a live ETA.
"""
import csv
import hashlib
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit
import httpx
from .models import Evidence
from .tools import ToolFailure
from .providers import now_utc

GUIDE = "https://datamall.lta.gov.sg/content/dam/datamall/datasets/LTA_DataMall_API_User_Guide.pdf"

# API paths/parameters come from the official guide, not model-generated URLs.
def spec(endpoint, label, ttl=120, kind="rows", parameters=(), required=(), basis="当前观测"):
    return dict(endpoint=endpoint,label=label,ttl=ttl,kind=kind,parameters=list(parameters),required=list(required),temporal_basis=basis)

DATASETS = {
    "bus_arrival": spec("v3/BusArrival","公交预计到站",20,"bus",("BusStopCode","ServiceNo"),("BusStopCode",)),
    "bus_services": spec("BusServices","公交运营与班次资料",86400,basis="运营资料，不是实时到站"),
    "bus_routes": spec("BusRoutes","公交线路与站序",86400,basis="运营资料，不是实时到站"),
    "bus_stops": spec("BusStops","公交站点",86400,basis="站点资料"),
    "passenger_bus": spec("PV/Bus","公交站历史客流",86400,"file",("Date",),basis="月度历史统计"),
    "passenger_od_bus": spec("PV/ODBus","公交起终点历史客流",86400,"file",("Date",),basis="月度历史统计"),
    "passenger_od_train": spec("PV/ODTrain","地铁起终点历史客流",86400,"file",("Date",),basis="月度历史统计"),
    "passenger_train": spec("PV/Train","地铁站历史客流",86400,"file",("Date",),basis="月度历史统计"),
    "taxi_availability": spec("Taxi-Availability","可载客出租车位置",60),
    "taxi_stands": spec("TaxiStands","出租车站",86400,basis="设施资料，不是叫车报价"),
    "train_alerts": spec("TrainServiceAlerts","地铁运营中断",60,"alerts"),
    "carpark_availability": spec("CarParkAvailabilityv2","停车位余量",60),
    "expressway_times": spec("EstTravelTimes","高速公路分段预计时间",300),
    "faulty_lights": spec("v2/FaultyTrafficLights","交通灯故障",120),
    "road_openings": spec("RoadOpenings","计划道路开放",3600,basis="计划资料，按实际生效日期使用"),
    "road_works": spec("RoadWorks","批准的道路施工",3600,basis="施工计划，按实际生效日期使用"),
    "traffic_images": spec("Traffic-Imagesv2","道路与口岸摄像头",60),
    "traffic_incidents": spec("TrafficIncidents","道路事故与阻塞",120),
    "speed_bands": spec("v4/TrafficSpeedBands","道路实时车速",300),
    "traffic_advisories": spec("VMS","道路信息牌提示",120),
    "bicycle_parking": spec("BicycleParkingv2","自行车停车设施",86400,"rows",("Lat","Long","Dist"),("Lat","Long"),"设施资料"),
    "geospatial": spec("GeospatialWholeIsland","全岛地图图层",86400,"file",("ID",),("ID",),"地图图层，不是实时交通"),
    "facilities_maintenance": spec("v2/FacilitiesMaintenance","地铁电梯维护",300),
    "station_crowd": spec("PCDRealTime","车站实时拥挤程度",600,"rows",("TrainLine",),("TrainLine",)),
    "station_crowd_forecast": spec("PCDForecast","车站拥挤预测",3600,"rows",("TrainLine",),("TrainLine",),"按预报日期和半小时时段匹配"),
    "traffic_flow": spec("TrafficFlow","历史道路流量",86400,"file",basis="季度历史统计，不是当前车速"),
    "planned_bus_routes": spec("PlannedBusRoutes","计划公交线路变更",3600,basis="计划资料，按实际生效日期使用"),
    "ev_charging": spec("EVChargingPoints","电动车充电设施与状态",300,"rows",("PostalCode",),("PostalCode",)),
    "ev_batch": spec("EVCBatch","全岛充电设施与状态",300,"file"),
    "flood_alerts": spec("PubFloodAlerts","积水预警",180),
    "gtfs_schedule": spec("GTFSScheduleTrain","地铁时刻与网络文件",86400,"file",basis="计划时刻，不是实时运行"),
    "gtfs_alerts": spec("GTFSRealTimeTrainServiceAlerts","地铁实时 GTFS 通告",60,"file"),
    "gtfs_updates": spec("GTFSRealtimeTrainTripUpdates","地铁中断期间 GTFS 班次更新",60,"file"),
}
TRAIN_LINES = {"CCL","CEL","CGL","DTL","EWL","NEL","NSL","BPL","SLRT","PLRT","TEL"}
GEOSPATIAL_LAYERS = ["ArrowMarking","Bollard","BusStopLocation","ControlBox","ConvexMirror","CoveredLinkWay","CyclingPath","DetectorLoop","ERPGantry","Footpath","GuardRail","KerbLine","LampPost","LaneMarking","ParkingStandardsZone","PassengerPickupBay","PedestrainOverheadbridge_UnderPass","RailConstruction","Railing","RetainingWall","RoadCrossing","RoadHump","RoadSectionLine","SchoolZone","SilverZone","SpeedRegulatingStrip","StreetPaint","TaxiStand","TrafficLight","TrafficSign","TrainStation","TrainStationExit","VehicularBridge_Flyover_Underpass","WordMarking"]

def catalogue():
    return {"documentation":GUIDE,"version":"6.10 (2026-10-01)","train_lines":sorted(TRAIN_LINES),"geospatial_layers":GEOSPATIAL_LAYERS,"datasets":[{"dataset":key,**value} for key,value in DATASETS.items()],
            "next_step":"按本次行程选择数据源；参数来自已取得的真实站点/线路。文件查询可读取 CSV、JSON、GTFS protobuf 或地理图层；权限失败与数据未知必须保留。"}

def clean(value):
    """Remove bearer-style signed URL queries before any result/cache/export."""
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
    if isinstance(value,list):return [clean(v) for v in value]
    if isinstance(value,str) and value.startswith(("https://","http://")):
        u=urlsplit(value);return urlunsplit((u.scheme,u.netloc,u.path,"",""))
    return value

def validate_parameters(dataset, parameters):
    if dataset not in DATASETS:raise ToolFailure("未知 LTA 数据集，请先查看数据目录。","invalid_arguments")
    s=DATASETS[dataset]
    if set(parameters)-set(s["parameters"]) or any(k not in parameters or parameters[k] in (None,"") for k in s["required"]):
        raise ToolFailure("LTA 参数不合法或缺少必填参数："+"、".join(s["required"]),"invalid_arguments")
    patterns={"BusStopCode":r"\d{5}","ServiceNo":r"[A-Za-z0-9]{1,12}","Date":r"\d{4}(?:0[1-9]|1[0-2])","PostalCode":r"\d{6}","ID":r"[A-Za-z][A-Za-z0-9]{0,80}"}
    for k,v in parameters.items():
        if k in patterns and not re.fullmatch(patterns[k],str(v)):raise ToolFailure("LTA 参数格式不合法："+k,"invalid_arguments")
        if k=="TrainLine" and v not in TRAIN_LINES:raise ToolFailure("未知 LTA 线路代码。","invalid_arguments")
        if k=="ID" and v not in GEOSPATIAL_LAYERS:raise ToolFailure("未知 LTA 地理图层代码。","invalid_arguments")
        if k in ("Lat","Long","Dist"):
            try:n=float(v)
            except (TypeError,ValueError):raise ToolFailure("LTA 坐标或半径参数无效。","invalid_arguments") from None
            lo,hi={"Lat":(1.10,1.50),"Long":(103.55,104.10),"Dist":(.1,5)}[k]
            if not lo<=n<=hi:raise ToolFailure("LTA 坐标或半径超出查询范围。","invalid_arguments")
    return s

def download(provider, url):
    # Only follow an actual official manifest link. Never send AccountKey to S3.
    u=urlsplit(url)
    allowed={"dmprod-datasets","dmgeospatial","ltafarecard","dm-traffic-flow-data","dm-traffic-camera-itsc"}
    if u.scheme!="https" or u.username or u.password or u.port not in (None,443) or not (u.hostname or "").endswith(".amazonaws.com") or (u.hostname or "").split(".")[0] not in allowed:
        raise ToolFailure("LTA 文件链接不是受支持的官方 HTTPS 下载来源。","invalid_response")
    provider.budget.http()
    try:
        chunks=[]; size=0
        with httpx.Client(timeout=min(30,max(.1,provider.budget.remaining())),trust_env=False,follow_redirects=False) as client:
            with client.stream("GET",url) as response:
                if response.status_code!=200:raise ToolFailure("LTA 文件下载失败，请重新获取有效链接。","provider_error")
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>25_000_000:raise ToolFailure("LTA 文件超过交互下载上限，可缩小范围或离线分析；没有将文件当作已读取。","download_limit")
                    provider.budget.check();chunks.append(chunk)
        return b"".join(chunks)
    except httpx.HTTPError:raise ToolFailure("LTA 文件下载网络不可用。","network_error") from None

def parse_file(data, path, limit, table=None, filters=None):
    """Bounded format decoding; ZIP entries are never extracted into user paths."""
    if path.endswith(".pb"):
        from google.transit import gtfs_realtime_pb2
        from google.protobuf.json_format import MessageToDict
        feed=gtfs_realtime_pb2.FeedMessage()
        try:
            feed.ParseFromString(data)
            if not feed.IsInitialized():raise ValueError()
        except Exception:raise ToolFailure("GTFS 实时文件无效。","invalid_response") from None
        return {"format":"gtfs_realtime","header":MessageToDict(feed.header,preserving_proto_field_name=True),
                "records":[MessageToDict(x,preserving_proto_field_name=True) for x in feed.entity[:limit]],"record_count":len(feed.entity),"truncated":len(feed.entity)>limit}
    if data[:2]==b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                members=z.infolist()
                if len(members)>100 or sum(x.file_size for x in members)>150_000_000 or any(x.flag_bits&1 for x in members):
                    raise ToolFailure("LTA 压缩文件超过读取上限或无法读取。","download_limit")
                tables={}; count=0
                for member in members:
                    if member.filename.endswith((".txt",".csv")):
                        if table and Path(member.filename).name!=table:continue
                        with z.open(member) as stream:
                            reader=csv.DictReader(io.TextIOWrapper(stream,encoding="utf-8-sig"));rows=[];total=0;matched=0;finished=True
                            for row in reader:
                                total+=1
                                if total>500_000:finished=False;break
                                if table and not all(str(row.get(k,""))==str(v) for k,v in (filters or {}).items()):continue
                                matched+=1
                                if len(rows)<limit:rows.append(row)
                        tables[member.filename]={"records":rows,"record_count":total if finished else None,"record_count_lower_bound":total,
                            "matched_count":matched if finished else None,"truncated":matched>limit or not finished,"scan_complete":finished};count+=total
                if tables:
                    result={"format":"csv_zip","tables":tables,"record_count":count if all(t["scan_complete"] for t in tables.values()) else None,
                        "record_count_lower_bound":count,"truncated":any(t["truncated"] for t in tables.values())}
                    if table:result["records"]=next(iter(tables.values()))["records"]
                    return result
                if table:raise ToolFailure("LTA 文件中没有该表，请使用实际返回的表名。","invalid_arguments")
                # Geospatial SHP files are exposed as actual geometry, not inferred coordinates.
                if any(x.filename.endswith(".shp") for x in members):
                    import geopandas as gpd
                    import pyogrio
                    shapes=[x for x in members if x.filename.endswith(".shp")]
                    chosen=shapes[0];stem=chosen.filename.rsplit(".",1)[0]
                    with tempfile.TemporaryDirectory(prefix="lta-shape-") as directory:
                        for member in members:
                            if member.filename.rsplit(".",1)[0]==stem and member.filename.rsplit(".",1)[-1] in {"shp","shx","dbf","prj","cpg"}:
                                # Write only chosen shape components under safe basenames.
                                target=Path(directory)/Path(member.filename.replace("\\","/")).name
                                target.write_bytes(z.read(member))
                        shape_path=Path(directory)/Path(chosen.filename.replace("\\","/")).name
                        count=pyogrio.read_info(shape_path)["features"]
                        frame=gpd.read_file(shape_path,rows=limit)
                    if frame.crs is None:raise ToolFailure("LTA 地图文件缺少坐标系，不能当作路线证据。","invalid_response")
                    features=json.loads(frame.head(limit).to_crs(4326).to_json(default=str))["features"]
                    return {"format":"geojson","records":features,"record_count":count,"truncated":count>limit or len(shapes)>1,"layers":[x.filename for x in shapes]}
        except (zipfile.BadZipFile,UnicodeError,ValueError,OSError):raise ToolFailure("LTA 压缩文件无法解析。","invalid_response") from None
        raise ToolFailure("LTA 压缩文件没有支持的数据格式。","invalid_response")
    try:payload=json.loads(data)
    except (ValueError,UnicodeError):raise ToolFailure("LTA 文件不是支持的 JSON、CSV ZIP、SHP ZIP 或 GTFS protobuf。","invalid_response") from None
    records=payload if isinstance(payload,list) else payload.get("value",[payload]) if isinstance(payload,dict) else None
    if isinstance(records,dict) and isinstance(records.get("evLocationsData"),list):records=records["evLocationsData"]
    if isinstance(records,list) and len(records)==1 and isinstance(records[0],dict) and isinstance(records[0].get("evLocationsData"),list):records=records[0]["evLocationsData"]
    if not isinstance(records,list):raise ToolFailure("LTA 文件数据结构无效。","invalid_response")
    return {"format":"json","records":clean(records[:limit]),"record_count":len(records),"truncated":len(records)>limit,
            "source_timestamp":payload.get("LastUpdatedTime",payload.get("LastUpdatedDate")) if isinstance(payload,dict) else None}

def query(provider, dataset, parameters=None, max_pages=1, skip=0, limit=50, read_file=True, filters=None, file_table=None):
    parameters=parameters or {};filters=filters or {};s=validate_parameters(dataset,parameters)
    if not 1<=max_pages<=8 or not 0<=skip<=100000 or not 1<=limit<=100 or len(filters)>4:
        raise ToolFailure("LTA 查询范围超出上限。","invalid_arguments")
    if file_table and (s["kind"]!="file" or not re.fullmatch(r"[A-Za-z0-9_-]+\.(?:txt|csv)",file_table)):
        raise ToolFailure("LTA 文件表名必须是实际 CSV/TXT 表名。","invalid_arguments")
    refs=[];records=[];complete=False;reason=None;pages=0;offset=skip;artifact=None
    for _ in range(max_pages):
        try:
            params=dict(parameters)
            if s["kind"]=="rows":params["$skip"]=offset
            payload,ev=provider.request("GET",provider.settings.lta_base.rstrip("/")+"/"+s["endpoint"],"LTA "+s["endpoint"],
                params=params,headers={"AccountKey":provider.key("LTA_API_KEY"),"accept":"application/json"},ttl=0 if s["kind"]=="file" or dataset=="traffic_images" else s["ttl"])
            refs.append(ev);pages+=1
            if s["kind"]=="file":
                provider.evidence[ev].valid_until=provider.evidence[ev].retrieved_at+timedelta(seconds=min(900,s["ttl"]))
            if not isinstance(payload,dict):raise ToolFailure("LTA 返回无效数据结构。","invalid_response")
            if s["kind"]=="bus":
                if not isinstance(payload.get("Services"),list):raise ToolFailure("LTA 未提供公交到站数据；不能据此认定公交停运。","no_observation")
                records=payload["Services"];complete=True;break
            if s["kind"]=="alerts":
                value=payload.get("value")
                if not isinstance(value,dict) or str(value.get("Status")) not in ("1","2"):
                    raise ToolFailure("LTA 地铁运营状态结构无效。","invalid_response")
                records=[value];complete=True;break
            batch=payload.get("value")
            if dataset=="ev_charging" and isinstance(batch,dict):batch=batch.get("evLocationsData")
            if not isinstance(batch,list) or any(not isinstance(x,dict) for x in batch):raise ToolFailure("LTA 数据记录无效。","invalid_response")
            if s["kind"]=="file":
                links=[row.get("Link") or row.get("link") for row in batch if row.get("Link") or row.get("link")]
                if len(links)!=1:raise ToolFailure("LTA 没有返回可用文件；权限或覆盖可能不足。","no_observation")
                artifact={"format":"manifest","download_host":urlsplit(links[0]).hostname,"read":False,
                          "source_timestamp":batch[0].get("timestamp")}
                if read_file:
                    key={"dataset":dataset,"parameters":parameters,"decoder_version":3,"file_table":file_table,"filters":filters if file_table else {}}
                    cached=provider.cache.get("LTA decoded file",key,s["ttl"])
                    if cached:
                        artifact=cached["parsed"];file_stamp=cached["retrieved_at"];digest=cached["sha256"]
                    else:
                        data=download(provider,links[0]);artifact=clean(parse_file(data,urlsplit(links[0]).path,100,file_table,filters))
                        file_stamp=now_utc().isoformat();digest=hashlib.sha256(data).hexdigest()
                        provider.cache.put("LTA decoded file",key,{"parsed":artifact,"retrieved_at":file_stamp,"sha256":digest})
                    from datetime import datetime
                    stamp=datetime.fromisoformat(file_stamp);eid="ev-lta-file-"+digest[:16]+"-"+str(int(stamp.timestamp()))
                    observed=None;valid_until=stamp+timedelta(seconds=s["ttl"])
                    if artifact.get("format")=="gtfs_realtime" and artifact.get("header",{}).get("timestamp"):
                        from datetime import timezone
                        observed=datetime.fromtimestamp(int(artifact["header"]["timestamp"]),timezone.utc)
                        valid_until=min(valid_until,observed+timedelta(seconds=s["ttl"]))
                        artifact["observation_fresh"]=valid_until>=now_utc()
                        if not artifact["observation_fresh"]:artifact["warning"]="GTFS 文件观测时间较早；读取成功不证明当前运行状态。"
                    provider.evidence[eid]=Evidence(id=eid,source="LTA file "+s["endpoint"],retrieved_at=stamp,valid_until=stamp+timedelta(seconds=s["ttl"]),
                        observed_at=observed,tool_call_id=provider.call_id,response_sha256=digest,summary=s["label"]+"真实文件解析")
                    provider.evidence[eid].valid_until=valid_until
                    refs.append(eid);artifact={**artifact,"read":True}
                    records=artifact.get("records",[])
                complete=True;break
            if len(batch)>500:raise ToolFailure("LTA 分页响应过大。","invalid_response")
            if dataset=="traffic_images":
                if not hasattr(provider,"lta_media"):provider.lta_media={}
                for row in batch:
                    if row.get("CameraID") is not None and row.get("ImageLink"):
                        provider.lta_media[str(row["CameraID"])]=row["ImageLink"]
            if batch and any(batch==records[i:i+len(batch)] for i in range(0,len(records),500)):
                raise ToolFailure("LTA 返回重复分页。","repeated_page")
            records.extend(batch);offset+=len(batch)
            if len(batch)<500:complete=True;break
        except ToolFailure as error:
            reason=error.code
            if not records:raise
            break
    stamp=min((provider.evidence[e].retrieved_at for e in refs),default=now_utc())
    selected=[row for row in records if all(str(row.get(k,""))==str(v) for k,v in filters.items())]
    result={"dataset":dataset,"label":s["label"],"parameters":parameters,"temporal_basis":s["temporal_basis"],"retrieved_at":stamp.isoformat(),
        "evidence_ids":refs,"records":clean(selected[:limit]),"record_count":artifact.get("record_count",len(records)) if artifact else len(records),"matched_count":len(selected),"truncated":len(selected)>limit or bool(artifact and artifact.get("truncated")),
        "collection":{"complete":complete,"pages":pages,"next_skip":offset if not complete else None,"reason":reason},"file":artifact,
        "next_step":"按时间、站点、线路及路线几何核对相关性。部分分页/截断不证明无匹配；实时到站不能预测远期经停后的班次。路况和事件不是新的路线几何。"}
    if not hasattr(provider,"lta_queries"):provider.lta_queries=[]
    # Retain all successfully retrieved page records for backend verification.
    # If a file only exposed a sample, it is not a complete absence check.
    if artifact and artifact.get("truncated"):result["next_step"]+=" 文件只向 Agent 展示有限内容，不能由未展示的记录推断没有相关设施/班次。"
    provider.lta_queries.append({**result,"records":clean(records)})
    return result

def camera_image(provider, camera_id):
    url=getattr(provider,"lta_media",{}).get(camera_id)
    if not url:raise ToolFailure("摄像头 ID 尚未由交通图像接口取得，请先查询真实记录。","unknown_id")
    data=download(provider,url)
    from PIL import Image,UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as picture:
            picture.verify()
            if picture.format not in ("JPEG","PNG"):raise ValueError()
            suffix=".jpg" if picture.format=="JPEG" else ".png"
    except (UnidentifiedImageError,ValueError,OSError):raise ToolFailure("LTA 摄像头未返回有效图像。","invalid_response") from None
    digest=hashlib.sha256(data).hexdigest();stamp=now_utc();eid="ev-lta-image-"+digest[:16]
    directory=provider.settings.root/"outputs"/"lta-media";directory.mkdir(parents=True,exist_ok=True)
    target=directory/(digest+suffix);target.write_bytes(data)
    provider.evidence[eid]=Evidence(id=eid,source="LTA traffic camera",retrieved_at=stamp,valid_until=stamp+timedelta(seconds=60),
        tool_call_id=provider.call_id,response_sha256=digest,summary="LTA 真实摄像头图像")
    return {"camera_id":camera_id,"path":str(target),"retrieved_at":stamp.isoformat(),"evidence_ids":[eid],"note":"现场摄像头参考，不由图片猜测排队时长或保证通关时间。"}
