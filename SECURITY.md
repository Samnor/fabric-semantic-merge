# Security

This experimental tool can create a model, submit refreshes, grant reviewed access and change report bindings. Review plans and access decisions before authorizing mutations. Keep exclusive control of affected report bindings and target refresh submissions throughout the operation. Separate GET/POST calls cannot exclude an external writer.

Azure CLI access tokens remain in memory. Exported definitions, DAX results, source references, access lists and journals can contain private data. Store them outside public repositories and do not attach raw run folders to issues. Restrictive file modes are best effort on platforms such as Windows; use an appropriately protected local directory.

For a vulnerability, use this repository's **Security → Report a vulnerability** feature once private vulnerability reporting is enabled by the maintainer. If unavailable, open an issue asking for a private contact channel without posting exploit details, tokens, business data or private model definitions. Do not test against other users' tenants or production models.

Only the latest released version receives fixes. No support period or security certification is promised. Do not use this tool as proof of effective-user RLS/OLS, complete semantic equivalence or safe concurrent cutover.
