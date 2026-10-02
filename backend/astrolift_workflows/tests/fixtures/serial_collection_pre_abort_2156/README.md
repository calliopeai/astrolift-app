# Previous collection command histories

`parent.json` and `item.json` were captured from the production
`WorkflowDefinitionRunWorkflow` and registered production activities at signed
App commit `8d598f31eff7a5646b3073d870cc7505b954f18e`, before the
`serial-collection-abort-v1` patch marker existed. The proof used the official
Temporal test server and a dedicated PostgreSQL 15.15 database.

The input contains one generic record (`archived record`) formatted by a native
serial collection. Both executions completed. These actual SDK histories pin
the original direct child wait and finalization sequence; the current replayer
must accept them as well as histories from the new abort-aware wait.
