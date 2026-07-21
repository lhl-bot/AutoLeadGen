# Product V2 local baseline

- Source HEAD before Product V2 work: `de779bc1dd85212e3a8af635535385a066c4c942`
- Branch: `feature/leadgen-ux-and-pipeline-fixes`
- Protected snapshot: `/Users/lhl/Desktop/开发/_autoleadgen_product_v2_backups/20260715-151353`
- Disabled credential-bearing script: `/Users/lhl/Desktop/开发/_autoleadgen_product_v2_quarantine/20260715-151353/deploy_server.sh.disabled`

The snapshot contains the complete source worktree (excluding reproducible dependency,
build, cache, and test-output directories) plus a binary Git patch. The disabled script
was copied before isolation and both copies have the same SHA-256. The quarantine and
backup directories are owner-readable only.

The Product V2 implementation baseline excludes the workflow 18 one-off scripts and
lists, the JYSK call document, and the product audit notebook/JSON. They remain preserved
in the external snapshot and must never be deployed or auto-run.

This phase is local-only. Production database, PM2, credentials, provider accounts,
webhooks, SMTP, LinkedIn, and WhatsApp are out of scope.

