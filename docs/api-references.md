# Supported interfaces

Checked against Microsoft documentation on 1 November 2026. Code uses public API paths only.

| Purpose | Interface |
| --- | --- |
| Azure CLI token | `az account get-access-token --resource AUDIENCE --output json` |
| Fabric audience | `https://api.fabric.microsoft.com` |
| Power BI audience | `https://analysis.windows.net/powerbi/api` |
| Workspace/model selection | Fabric `GET /v1/workspaces`, `GET /v1/workspaces/{workspace}/semanticModels` |
| Deployment preflight | Fabric `GET /v1/workspaces/{workspace}`, `GET /v1/connections/{connection}` and Power BI `GET .../groups/{workspace}`, `GET .../datasets/{model}` |
| Definition export | Fabric `POST .../semanticModels/{model}/getDefinition?format=TMSL` |
| Creation | Fabric `POST .../semanticModels`, with `model.bim` and `definition.pbism` InlineBase64 parts |
| Async polling | Fabric `GET /v1/operations/{id}` then `/result` |
| Existing source connections | Fabric `GET .../items/{model}/connections` |
| Connection binding | Fabric `POST .../semanticModels/{model}/bindConnection` |
| Source/target access | Power BI `GET/POST /v1.0/myorg/groups/{workspace}/datasets/{model}/users` |
| Refresh | Power BI `POST .../datasets/{model}/refreshes`; exact-ID execution-detail polling |
| Schedule observation | Power BI `GET .../datasets/{model}/refreshSchedule`; proposals only, no schedule PATCH |
| Validation | Power BI `POST .../datasets/{model}/executeQueries` |
| Tenant report inventory | Power BI `GET /v1.0/myorg/admin/reports?$top=5000&$skip=N` |
| Scoped report inventory | Power BI `GET /v1.0/myorg/groups/{workspace}/reports` and `/reports` for My workspace |
| Report binding/readback | Power BI `POST .../reports/{report}/Rebind`, followed by `GET .../reports/{report}` |

Source model export/connections need write access. A target workspace needs appropriate license/capacity and Contributor or greater permissions. Tenant-wide report inventory needs Fabric admin permissions (or eligible service-principal configuration). Report rebinding needs report Write and target-model Build permissions. Execute Queries must be enabled, and its service-principal/RLS/SSO limitations apply. User/Group dataset grants do not configure workspace Write permissions or role memberships. No tenant setting, license or access role is changed by the CLI.

Primary documentation:

- [Azure CLI account token command](https://learn.microsoft.com/en-us/cli/azure/account#az-account-get-access-token)
- [Get semantic model definition](https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/get-semantic-model-definition)
- [Definition formats and parts](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/semantic-model-definition)
- [Tabular calculated-table column types and inferred names](https://learn.microsoft.com/en-us/openspecs/sql_server_protocols/ms-ssas-t/00a9ec7a-5f4d-4517-8091-b370fe2dc18b)
- [Create semantic model](https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/create-semantic-model)
- [Fabric long-running operations](https://learn.microsoft.com/en-us/rest/api/fabric/articles/long-running-operation)
- [List item connections](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/list-item-connections)
- [Get connection metadata](https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/get-connection)
- [Get Fabric workspace](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/get-workspace)
- [Get Power BI workspace/capacity flag](https://learn.microsoft.com/en-us/rest/api/power-bi/groups/get-group)
- [Get original Power BI model metadata](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-dataset-in-group)
- [Bind semantic model connection](https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/bind-semantic-model-connection)
- [Tenant report inventory](https://learn.microsoft.com/en-us/rest/api/power-bi/admin/reports-get-reports-as-admin)
- [Rebind a report in a workspace](https://learn.microsoft.com/en-us/rest/api/power-bi/reports/rebind-report-in-group)
- [Rebind a My workspace report](https://learn.microsoft.com/en-us/rest/api/power-bi/reports/rebind-report)
- [Refresh dataset](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/refresh-dataset-in-group)
- [Observe exact refresh execution](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-execution-details-in-group)
- [Bounded refresh history for standard before/after tracking](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history-in-group)
- [Get refresh schedule in group](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-schedule-in-group)
- [Refresh schedule request schema](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/update-refresh-schedule-in-group) (used only for proposed JSON; no writer implemented)
- [Scheduled refresh limits and timing](https://learn.microsoft.com/en-us/power-bi/connect-data/refresh-scheduled-refresh)
- [Execute Queries](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries-in-group)
- [Get dataset users](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-dataset-users-in-group)
- [Grant dataset access](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/post-dataset-user-in-group)
