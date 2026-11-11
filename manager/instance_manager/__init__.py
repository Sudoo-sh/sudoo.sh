"""Odoo Instance Manager: runs independent Odoo demos on one Docker server and manages them from a web panel.

Created by Yousef M. Y. Al Sabbah.

Module map:
    settings      every setting, read once from the environment
    shell         running external commands
    instances     instance folders, their .env files, names and ports
    state         persistent per-instance state (last visit, paused, switched off)
    jobs          background actions, one at a time per instance
    odoo          Odoo configuration, database parameters, readiness checks
    gitrepo       git operations on an instance's code
    github        deploy keys, git access configuration, repository access checks
    tunnels       Cloudflare quick-tunnel connectors (panel and one per instance)
    gateway       receives every visit to an instance, wakes paused instances
    status_pages  pages the gateway shows while an instance is not available
    monitor       resource usage and instance status
    operations    what the panel can do with an instance (create, restart, update, back up, delete, ...)
    backups       Odoo-format backups, taken before every deletion
    background    scheduled work (monitor, automatic pause, automatic deletion, GitHub polling)
    accounts      panel accounts, password hashing, sign-in throttling
    audit         audit log
    hints         plain-language explanations of technical errors
    web           the Flask web panel and its JSON API
"""
