#!/usr/bin/env python3
"""Allocation de port gRPC déterministe par service (utilisé par make start-services)."""
import sys

PORTS = {
    "parser": 50051,
    "ai_engine": 50052,
    "simulator": 50053,
    "router": 50054,
    "drc_dfm_engine": 50055,
    "firmware_bridge": 50056,
    "exporter": 50057,
    "orchestrator": 50060,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "parser"
    print(PORTS.get(name, 50099))
