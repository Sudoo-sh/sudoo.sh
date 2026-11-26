# Workflow: Kubernetes engine (beta)

[العربية](11-kubernetes-engine-beta.ar.md) · [All workflows](README.md)

**Goal:** run an instance on Kubernetes (k3s) instead of Docker, and move it between the two.

**Who can do it:** a server administrator (root on the panel's server) installs k3s. Moving an instance needs the `instances.move` permission.

> **This is a beta.** Try it on a server you can rebuild. Docker stays the default engine, and existing instances stay on Docker until you move them. The Kubernetes engine is off unless the operator sets KUBERNETES_ENGINE_BETA=true.

## What it does not support yet
Enterprise instances, the mail catcher, Connect as, the code profiler, database upgrades, project builds and imports do not work on Kubernetes instances yet. Moving an Enterprise instance to Kubernetes is refused. *(Code: operations.change_engine; the rest is from the Admin guide.)*

## Steps
1. Check the server: at least 8 GB of memory (k3s itself takes about 1 to 1.5 GB), Ubuntu or Debian, and the panel installed.
2. On the panel's server, as root, run the installer's engine command (the Admin guide, section 33, gives it). It installs k3s, prepares the cluster for the panel and turns the beta on. Read what it asks before you agree.
3. **New instance** now shows **Engine**: Docker or Kubernetes (beta).
4. To move an existing instance, open it and choose **Move to another engine**. Production asks you to confirm.
5. **Downtime:** the instance is switched off for the move. Visitors see a "switched off" page until it ends. That takes about as long as a backup plus a restore, which depends on the size of the data.
6. The panel switches it off, takes a backup ("Before moving from X to Y"), removes it from its engine, creates it on the other one, restores the backup and starts it. Its name, link, database, files and passwords stay. *(Code: operations.change_engine.)*
7. If it does not start on the new engine, the panel puts it back on the old one from the same backup and the job fails with a message saying so.

## Adding nodes
In Settings, **Kubernetes (beta)** lists the cluster's nodes. **Add a node** needs several servers (a premium feature of Business plans) and shows a command to run once on the new server with a join token that works for a limited time (KUBERNETES_JOIN_TOKEN_MINUTES). Open the needed ports between your servers only, never to the internet.

## Know before you rely on it
- A volume is a folder on its node. If that node is lost, the data of its instances is lost with it. More nodes give more room, not high availability. Keep your usual backups.
- The first instance on a node starts more slowly because it downloads the images.
- Removing k3s deletes the data of every Kubernetes instance. Move them to Docker first.

> No screenshots yet: the demo does not include the Kubernetes engine.

## What you see when it worked
The instance shows "Runs on Kubernetes (beta)" and answers at its usual link.

## Common problems
- The Engine choice is missing: the beta is not turned on.
- The move failed and the instance is back on Docker: read the job's log, fix the cause on the cluster, and try again.

## Review limits
Move steps and failure handling are checked in code (operations.change_engine, _leave_engine, _arrive_on_engine). The installer's behaviour, the k3s version, the ports, the memory figures and the node join are from the Admin guide only: the installer source was not verified. Nothing was run.
