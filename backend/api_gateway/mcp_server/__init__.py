"""Serveur MCP — catalogue d'outils typés pour agents externes (sous-paquet).

`tools.py` : les 5 outils (create_project, run_pipeline, get_design_state,
apply_surgical_edit, export_gerbers) sous forme de dataclass McpTool.
`server.py` : dispatch JSON-RPC 2.0 (initialize, tools/list, tools/call) —
utilisables par Claude, Cursor ou Devin via stdio.
"""
