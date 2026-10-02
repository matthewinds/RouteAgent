"""Explicit historical SYNTHETIC fixture. Not called by the normal UI or service."""
import argparse
from route_agent.fixtures import plan_route

def main():
    parser=argparse.ArgumentParser(description="Historical synthetic fixture mechanism test ONLY.")
    parser.add_argument("text")
    parser.add_argument("--snapshot",default="demo-replan")
    args=parser.parse_args()
    result=plan_route(args.text,{"departure_time":"2026-10-01T09:00:00+08:00"},"replay",args.snapshot)
    print("SYNTHETIC HISTORICAL TEST — NOT LIVE ACCEPTANCE")
    print(result.model_dump_json(indent=2))

if __name__=="__main__": main()
