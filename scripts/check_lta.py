"""Bounded DataMall connectivity checks, never a route planner or offline fallback."""
import argparse
import json
from dataclasses import replace
from pathlib import Path
from route_agent.config import Settings
from route_agent.lta_data import DATASETS,catalogue,query
from route_agent.providers import Providers
from route_agent.tools import Budget,ToolFailure

def main():
    parser=argparse.ArgumentParser(description="检查 LTA 动态数据；默认只列目录，不调用服务。")
    parser.add_argument("--all",action="store_true",help="使用公开样例参数逐项做小范围检查；消耗 API 配额")
    parser.add_argument("--dataset",choices=DATASETS)
    parser.add_argument("--parameters",default="{}",help="该数据集的官方参数 JSON；不要传 Key")
    parser.add_argument("--read-files",action="store_true",help="同时读取文件；大小/行数有限，部分读取保持明确")
    args=parser.parse_args()
    if not args.all and not args.dataset:
        print(json.dumps(catalogue(),ensure_ascii=False,indent=2));return 0
    settings=replace(Settings(),max_seconds=900,max_http_calls=150);provider=Providers(settings,Budget(settings))
    # Connectivity examples only, never used as addresses in production planning.
    samples={"bus_arrival":{"BusStopCode":"83139"},"geospatial":{"ID":"TrainStation"},
        "bicycle_parking":{"Lat":1.364897,"Long":103.766094,"Dist":.5},"station_crowd":{"TrainLine":"DTL"},
        "station_crowd_forecast":{"TrainLine":"DTL"},"ev_charging":{"PostalCode":"018956"}}
    report=[]
    for dataset in DATASETS if args.all else [args.dataset]:
        try:
            result=query(provider,dataset,parameters=samples.get(dataset,{}) if args.all else json.loads(args.parameters),read_file=args.read_files,limit=2)
            file=result.get("file") or {}
            entry={"dataset":dataset,"label":DATASETS[dataset]["label"],"status":"available","retrieved_at":result["retrieved_at"],
                "record_count":result["record_count"],"collection":result["collection"],"evidence_ids":result["evidence_ids"],
                "file":{k:file.get(k) for k in ("format","read","record_count","truncated","observation_fresh") } if file else None}
        except ToolFailure as error:entry={"dataset":dataset,"status":error.code,"message":str(error)}
        report.append(entry);print(json.dumps(entry,ensure_ascii=False),flush=True)
    directory=settings.root/"outputs"/"lta-check";directory.mkdir(parents=True,exist_ok=True)
    (directory/"status.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return int(any(x["status"]!="available" for x in report))

if __name__=="__main__":raise SystemExit(main())
