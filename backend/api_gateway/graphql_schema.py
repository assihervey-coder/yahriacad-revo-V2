"""Schéma GraphQL de la gateway — graphql-core si dispo, sinon descriptif.

- graphql-core présent : schéma exécutable (Query.design/status, Mutation.
  runPipeline) dont les resolvers passent par la couche runtime ;
- graphql-core ABSENT : docstring + dict de types (SCHEMA_SDL/TYPES) — le
  contrat reste documenté, les tests (et compileall) passent partout.
"""

from __future__ import annotations

from typing import Any

SCHEMA_SDL = '''
type Design {
  id: ID!
  version: Int!
  boardJson: String!        # Board sérialisé (common.design_model)
  metrics: String!          # drc_score, vias, nets non routés...
}
type PipelineStatus {
  pipelineId: ID!
  projectId: ID!
  state: String!            # running | done | failed
  steps: String!            # JSON des 8 StepStatus (proto orchestrator)
}
type Query {
  design(id: ID!): Design
  status(id: ID!): PipelineStatus
}
type Mutation {
  runPipeline(id: ID!, steps: [Int!]): PipelineStatus
}
'''

# Dict descriptif — contrat consommable même sans graphql-core
TYPES: dict[str, Any] = {
    "Design": {"fields": ["id: ID!", "version: Int!", "boardJson: String!",
                          "metrics: String!"],
               "resolver": "runtime.get_design_state(id)"},
    "PipelineStatus": {"fields": ["pipelineId: ID!", "projectId: ID!",
                                  "state: String!", "steps: String!"],
                       "resolver": "runtime.get_run(id)"},
    "Query": {"fields": ["design(id: ID!): Design", "status(id: ID!): PipelineStatus"]},
    "Mutation": {"fields": ["runPipeline(id: ID!, steps: [Int!]): PipelineStatus"]},
}


def _json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, default=str)


def build_schema(runtime: Any):
    """Construit le GraphQLSchema exécutable — None si graphql-core absent."""
    try:
        import graphql  # noqa: F401 — disponibilité testée par la construction
        from graphql import (GraphQLArgument, GraphQLField, GraphQLID,
                             GraphQLInt, GraphQLList, GraphQLObjectType,
                             GraphQLSchema, GraphQLString)
    except ImportError:
        return None

    def resolve_design(_root: Any, _info: Any, id: str) -> dict[str, Any]:  # noqa: A002
        return {"id": id, "version": runtime.get_design_state(id)["version"],
                "boardJson": _json(runtime.get_design_state(id)["board"]),
                "metrics": _json(runtime.state_manager.metrics(id))}

    def resolve_status(_root: Any, _info: Any, id: str) -> dict[str, Any]:
        run = runtime.get_run(id)
        return {"pipelineId": run["pipeline_id"], "projectId": run["project_id"],
                "state": run["state"], "steps": _json(run["statuses"])}

    def resolve_run_pipeline(_root: Any, _info: Any, id: str,
                             steps: list[int] | None = None) -> dict[str, Any]:
        run = runtime.run_pipeline(id, steps=steps)
        return {"pipelineId": run["pipeline_id"], "projectId": run["project_id"],
                "state": run["state"], "steps": _json(run["statuses"])}

    design_type = GraphQLObjectType(
        "Design", lambda: {
            "id": GraphQLField(GraphQLID),
            "version": GraphQLField(GraphQLInt),
            "boardJson": GraphQLField(GraphQLString),
            "metrics": GraphQLField(GraphQLString),
        })
    status_type = GraphQLObjectType(
        "PipelineStatus", lambda: {
            "pipelineId": GraphQLField(GraphQLID),
            "projectId": GraphQLField(GraphQLID),
            "state": GraphQLField(GraphQLString),
            "steps": GraphQLField(GraphQLString),
        })
    query = GraphQLObjectType("Query", lambda: {
        "design": GraphQLField(design_type, args={"id": GraphQLArgument(GraphQLID)},
                               resolve=resolve_design),
        "status": GraphQLField(status_type, args={"id": GraphQLArgument(GraphQLID)},
                               resolve=resolve_status),
    })
    mutation = GraphQLObjectType("Mutation", lambda: {
        "runPipeline": GraphQLField(
            status_type,
            args={"id": GraphQLArgument(GraphQLID),
                  "steps": GraphQLArgument(GraphQLList(GraphQLInt))},
            resolve=resolve_run_pipeline),
    })
    return GraphQLSchema(query=query, mutation=mutation)


def execute_query(runtime: Any, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Exécute une requête GraphQL — erreur explicite si graphql-core absent."""
    schema = build_schema(runtime)
    if schema is None:
        return {"errors": [{"message":
                "graphql-core absent — schéma descriptif uniquement (voir SCHEMA_SDL/TYPES)"}]}
    from graphql import graphql_sync
    result = graphql_sync(schema, query, variable_values=variables)
    return {"data": result.data,
            "errors": [e.formatted for e in (result.errors or [])]}
