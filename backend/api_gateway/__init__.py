"""Point d'entrée unique : REST + GraphQL + WebSocket + MCP (section 04).

La gateway FastAPI agrège :
- les routers REST (projects, pipeline, design, edits, credits, exports, files) ;
- le schéma GraphQL (graphql_schema.py) ;
- le canal WebSocket /ws (push des événements du state_manager, reprise par
  delta via ?since_seq=, latence cible < 100 ms) ;
- le serveur MCP (JSON-RPC 2.0 sur stdio — mcp_server/) pour agents externes
  (Claude, Cursor, Devin) ;
- les middlewares auth (JWT HS256 + clés API) et rate_limit (fenêtre glissante).

Les dépendances optionnelles (fastapi, pydantic, graphql-core, python-jose,
prometheus_client) sont TOUTES fallback : si absentes, des stubs minimalistes
documentés (compat.py) gardent le package importable et testable partout.
"""
