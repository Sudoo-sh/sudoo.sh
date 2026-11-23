"""The topology of this panel (servers or nodes, projects, instances and their parts, traffic between them), for
the Sudoo.sh staff's live infrastructure diagram, shared only when an administrator turns it on.

    contract.py   the versioned snapshot ("sudoo.topology/v1") and the engine-neutral records engines report
    engines.py    where the figures come from: Docker today, any engine that registers itself (Kubernetes)
    privacy.py    pseudonymous ids
    builder.py    the snapshot, cached for a few seconds
    share.py      the signed sending loop (SHARE_TOPOLOGY_WITH_SUDOO), and withdrawing it when switched off

See docs/MARKETPLACE.md "Sharing the topology" and docs/SECURITY.md.
"""
