"""Start plane-mcp-server against a self-hosted Plane Community Edition (v1.4.x).

Why: plane-mcp-server (every release up to 0.3.4) sends work-item relations to
`/work-items/<id>/dependencies/` and `/work-items/<id>/work-item-relations/`, which only exist in
Plane Cloud / commercial editions. Plane CE v1.4.2 answers 404 there; its public API has
`GET|POST /api/v1/workspaces/<ws>/projects/<p>/work-items/<id>/relations/` instead.

This shim reroutes the SDK's dependency calls to that endpoint, then runs the normal server, so
the `workitem_relation` tool works unchanged:
  - list   -> GET  .../relations/ (blocking, blocked_by, start_*, finish_*, duplicate, relates_to)
  - create -> POST .../relations/ {"relation_type": ..., "issues": [...]}
  - custom relations (not in CE) list as empty; creating or removing one returns a clear error.
Removing a relation is not in CE's public API (and the agent-ops guard blocks deletes anyway).

Run with the pinned server version so the patched names exist:
  uvx --from plane-mcp-server==0.3.4 python plane-mcp-ce.py stdio
"""

from __future__ import annotations

from typing import Any

from plane.api.work_items.custom_relations import WorkItemCustomRelations
from plane.api.work_items.dependencies import WorkItemDependencies
from plane.models.work_items import WorkItemDependencyResponse, WorkItemWithRelationType

_CE_ONLY = (
    "not supported by this Plane Community Edition server (v1.4): only built-in relations "
    "(relation_type blocking/blocked_by/start_before/start_after/finish_before/finish_after) "
    "can be created, and relations are removed by a human in the Plane UI"
)


def _relations_path(workspace_slug: str, project_id: str, work_item_id: str) -> str:
    return f"{workspace_slug}/projects/{project_id}/work-items/{work_item_id}/relations/"


def _describe(resource: WorkItemDependencies, workspace_slug: str, ref: dict[str, Any], kind: str) -> dict:
    """CE lists only {project_id, issue_id}; add name and sequence id so agents can cite keys like BIL-<n>."""
    item: dict[str, Any] = {"id": ref.get("issue_id"), "project_id": ref.get("project_id"), "relation_type": kind}
    try:
        detail = resource._get(f"{workspace_slug}/projects/{ref['project_id']}/work-items/{ref['issue_id']}/")
        item.update(name=detail.get("name"), sequence_id=detail.get("sequence_id"), state_id=detail.get("state"))
    except Exception:  # noqa: BLE001 - the ids alone are still a correct answer
        pass
    return item


def _dependencies_list(self, workspace_slug: str, project_id: str, work_item_id: str):
    response = self._get(_relations_path(workspace_slug, project_id, work_item_id))
    grouped = {
        kind: [WorkItemWithRelationType.model_validate(_describe(self, workspace_slug, ref, kind)) for ref in refs]
        for kind, refs in response.items()
    }
    return WorkItemDependencyResponse.model_validate(grouped)


def _dependencies_create(self, workspace_slug: str, project_id: str, work_item_id: str, data):
    response = self._post(
        _relations_path(workspace_slug, project_id, work_item_id),
        {"relation_type": data.relation_type, "issues": list(data.work_item_ids)},
    )
    return [WorkItemWithRelationType.model_validate(item) for item in (response or [])]


def _unsupported(what: str):
    def method(self, *args, **kwargs):
        raise RuntimeError(f"{what} is {_CE_ONLY}")

    return method


WorkItemDependencies.list = _dependencies_list
WorkItemDependencies.create = _dependencies_create
WorkItemDependencies.remove = _unsupported("removing a relation")
WorkItemCustomRelations.list = lambda self, *args, **kwargs: {}
WorkItemCustomRelations.create = _unsupported("a custom relation")
WorkItemCustomRelations.remove = _unsupported("removing a custom relation")

if __name__ == "__main__":
    from plane_mcp.__main__ import main

    main()
