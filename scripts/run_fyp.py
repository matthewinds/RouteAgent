"""Normal CLI calls only the online DeepSeek tool agent."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from route_agent import plan_route

def main():
    parser=argparse.ArgumentParser(description="DeepSeek real online route planning; requires .env keys.")
    parser.add_argument("text")
    parser.add_argument("--departure",default=None,help="ISO datetime, defaults to now in Singapore")
    parser.add_argument("--travel-mode",choices=["driving","walking","drive_walk"])
    args=parser.parse_args()
    clarification={"departure_time":args.departure or datetime.now(ZoneInfo("Asia/Singapore")).isoformat()}
    if args.travel_mode: clarification["mode"]=args.travel_mode
    result=plan_route(args.text,clarification)
    print(result.model_dump_json(indent=2))
    return 2 if result.status=="tool_error" else 0

if __name__=="__main__":
    raise SystemExit(main())
