#!/usr/bin/env bash
# Sudoo.sh: a hardened single-node k3s server for the Kubernetes engine (beta), and the panel's access to it.
#
# Run it as root on the server where the panel runs (docs/ADMIN_GUIDE.md, "Kubernetes engine (beta)"):
#
#     sudo bash scripts/install-k3s.sh [--version v1.36.6+k3s1] [--odoo-root /opt/odoo]
#     bash scripts/install-k3s.sh --print-manifests       # only print the panel's access (RBAC and policy)
#
# What it does:
#   1. installs k3s (pinned version) with Pod Security "restricted" as every namespace's default, Secrets encrypted
#      at rest, an audit log, Traefik and the service load balancer switched off (the panel's gateway serves
#      visitors), and the API, NodePorts and kubelet only where they must be;
#   2. creates the namespace sudoo-system, the panel's ServiceAccount sudoo-panel and its rights: its own namespaces
#      only (ClusterRoles sudoo-panel and sudoo-instance, Role sudoo-join, ValidatingAdmissionPolicy
#      sudoo-panel-scope);
#   3. writes the panel's kubeconfig (JSON, the ServiceAccount's token) to <ODOO_ROOT>/manager/kubernetes/;
#   4. prints the next step (the .env lines for the panel).
#
# It changes nothing else on the server. Running it again updates the access and the files; k3s is installed only
# when it is missing.
set -euo pipefail

K3S_VERSION="v1.36.6+k3s1"
ODOO_ROOT="/opt/odoo"
PRINT_ONLY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --version) K3S_VERSION="$2"; shift 2 ;;
        --odoo-root) ODOO_ROOT="$2"; shift 2 ;;
        --print-manifests) PRINT_ONLY=1; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done

manifests() {
cat <<'YAML'
apiVersion: v1
kind: Namespace
metadata:
  name: sudoo-system
  labels:
    app.kubernetes.io/part-of: sudoo
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/enforce-version: latest
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: sudoo-panel
  namespace: sudoo-system
automountServiceAccountToken: false
---
apiVersion: v1
kind: Secret
metadata:
  name: sudoo-panel-token
  namespace: sudoo-system
  annotations:
    kubernetes.io/service-account.name: sudoo-panel
type: kubernetes.io/service-account-token
---
# Cluster-wide: its own namespaces (the policy below limits the names), reading nodes, binding sudoo-instance.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: sudoo-panel
rules:
  - apiGroups: [""]
    resources: [namespaces]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [""]
    resources: [nodes]
    verbs: [get, list, watch]
  - apiGroups: [metrics.k8s.io]
    resources: [nodes]
    verbs: [get, list]
  - apiGroups: [rbac.authorization.k8s.io]
    resources: [rolebindings]
    verbs: [get, list, create, update, patch, delete]
  - apiGroups: [rbac.authorization.k8s.io]
    resources: [clusterroles]
    verbs: [bind]
    resourceNames: [sudoo-instance]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: sudoo-panel
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: sudoo-panel
subjects:
  - kind: ServiceAccount
    name: sudoo-panel
    namespace: sudoo-system
---
# What the panel may do inside each of its namespaces: bound there by the panel itself (a RoleBinding).
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: sudoo-instance
rules:
  - apiGroups: [""]
    resources: [pods, services, configmaps, secrets, persistentvolumeclaims, resourcequotas, limitranges]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [""]
    resources: [pods/exec, pods/log]
    verbs: [get, create]
  - apiGroups: [apps]
    resources: [deployments, statefulsets]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [batch]
    resources: [jobs]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [networking.k8s.io]
    resources: [networkpolicies]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [metrics.k8s.io]
    resources: [pods]
    verbs: [get, list]
---
# kube-system: creating k3s join tokens only (it cannot read any Secret there).
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: sudoo-join
  namespace: kube-system
rules:
  - apiGroups: [""]
    resources: [secrets]
    verbs: [create]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: sudoo-join
  namespace: kube-system
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: sudoo-join
subjects:
  - kind: ServiceAccount
    name: sudoo-panel
    namespace: sudoo-system
---
# What role-based access cannot say: which names. Every change the panel's ServiceAccount asks for is checked here.
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicy
metadata:
  name: sudoo-panel-scope
spec:
  failurePolicy: Fail
  matchConstraints:
    resourceRules:
      - apiGroups: [""]
        apiVersions: ["*"]
        operations: [CREATE, UPDATE, DELETE]
        resources: [namespaces]
      - apiGroups: [rbac.authorization.k8s.io]
        apiVersions: ["*"]
        operations: [CREATE, UPDATE, DELETE]
        resources: [rolebindings]
      - apiGroups: [""]
        apiVersions: ["*"]
        operations: [CREATE, UPDATE]
        resources: [secrets]
  matchConditions:
    - name: the-panel
      expression: "request.userInfo.username == 'system:serviceaccount:sudoo-system:sudoo-panel'"
  variables:
    - name: target
      expression: "request.operation == 'DELETE' ? oldObject : object"
    - name: ours
      expression: >-
        request.resource.resource != 'namespaces' ||
        (variables.target.metadata.name.startsWith('sudoo-') && variables.target.metadata.name != 'sudoo-system' &&
         has(variables.target.metadata.labels) &&
         variables.target.metadata.labels.exists(k, k == 'app.kubernetes.io/managed-by') &&
         variables.target.metadata.labels['app.kubernetes.io/managed-by'] == 'sudoo')
  validations:
    - expression: "variables.ours"
      message: "The panel manages only namespaces named sudoo-<instance> with app.kubernetes.io/managed-by=sudoo."
    - expression: >-
        request.resource.resource != 'namespaces' || request.operation == 'DELETE' ||
        (variables.target.metadata.labels.exists(k, k == 'pod-security.kubernetes.io/enforce') &&
         variables.target.metadata.labels['pod-security.kubernetes.io/enforce'] == 'restricted')
      message: "The panel's namespaces keep Pod Security enforce=restricted."
    - expression: >-
        request.resource.resource != 'rolebindings' ||
        (request.namespace.startsWith('sudoo-') && request.namespace != 'sudoo-system' &&
         (request.operation == 'DELETE' ||
          (object.roleRef.kind == 'ClusterRole' && object.roleRef.name == 'sudoo-instance')))
      message: "The panel binds only sudoo-instance, and only in its own namespaces."
    - expression: >-
        request.resource.resource != 'secrets' || request.namespace != 'kube-system' ||
        (object.type == 'bootstrap.kubernetes.io/token' &&
         object.metadata.name.startsWith('bootstrap-token-') &&
         has(object.data) &&
         object.data.exists(k, k == 'expiration') &&
         object.data.exists(k, k == 'auth-extra-groups') &&
         object.data['auth-extra-groups'] == b'system:bootstrappers:k3s:default-node-token')
      message: "In kube-system the panel creates k3s join tokens only, with an expiry."
---
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicyBinding
metadata:
  name: sudoo-panel-scope
spec:
  policyName: sudoo-panel-scope
  validationActions: [Deny]
YAML
}

if [ "$PRINT_ONLY" = 1 ]; then
    manifests
    exit 0
fi

if [ "$(id -u)" != 0 ]; then
    echo "Run it as root: sudo bash $0" >&2
    exit 1
fi

CONFIG_DIR=/etc/rancher/k3s
mkdir -p "$CONFIG_DIR" /var/log/k3s
chmod 700 /var/log/k3s

# Pod Security "restricted" for every namespace by default (kube-system keeps what k3s needs)
cat > "$CONFIG_DIR/pod-security.yaml" <<'YAML'
apiVersion: apiserver.config.k8s.io/v1
kind: AdmissionConfiguration
plugins:
  - name: PodSecurity
    configuration:
      apiVersion: pod-security.admission.config.k8s.io/v1
      kind: PodSecurityConfiguration
      defaults:
        enforce: restricted
        enforce-version: latest
        audit: restricted
        audit-version: latest
        warn: restricted
        warn-version: latest
      exemptions:
        usernames: []
        runtimeClasses: []
        namespaces: [kube-system]
YAML

# The audit log: who changed what (metadata only, never Secrets' contents)
cat > "$CONFIG_DIR/audit-policy.yaml" <<'YAML'
apiVersion: audit.k8s.io/v1
kind: Policy
omitStages: [RequestReceived]
rules:
  - level: None
    verbs: [get, list, watch]
  - level: Metadata
YAML

cat > "$CONFIG_DIR/config.yaml" <<YAML
# Written by Sudoo.sh scripts/install-k3s.sh
secrets-encryption: true
protect-kernel-defaults: false
write-kubeconfig-mode: "0600"
disable:
  - traefik
  - servicelb
kube-apiserver-arg:
  - "admission-control-config-file=$CONFIG_DIR/pod-security.yaml"
  - "audit-policy-file=$CONFIG_DIR/audit-policy.yaml"
  - "audit-log-path=/var/log/k3s/audit.log"
  - "audit-log-maxage=30"
  - "audit-log-maxbackup=10"
  - "audit-log-maxsize=100"
  - "anonymous-auth=false"
kube-proxy-arg:
  - "nodeport-addresses=127.0.0.1/32"
kubelet-arg:
  - "read-only-port=0"
YAML

if ! command -v k3s >/dev/null 2>&1; then
    echo "Installing k3s $K3S_VERSION"
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S_VERSION" sh -s - server
else
    echo "k3s is installed already ($(k3s --version | head -n 1)); restarting it with the configuration above"
    systemctl restart k3s
fi

KUBECTL="k3s kubectl"
echo "Waiting for the cluster"
for _ in $(seq 1 60); do
    $KUBECTL get --raw /readyz >/dev/null 2>&1 && break
    sleep 2
done

echo "Creating the panel's access (namespace sudoo-system, ServiceAccount sudoo-panel)"
manifests | $KUBECTL apply -f -

TOKEN=""
for _ in $(seq 1 30); do
    TOKEN=$($KUBECTL -n sudoo-system get secret sudoo-panel-token -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || true)
    [ -n "$TOKEN" ] && break
    sleep 1
done
if [ -z "$TOKEN" ]; then
    echo "The ServiceAccount's token was not created." >&2
    exit 1
fi
CA=$($KUBECTL -n sudoo-system get configmap kube-root-ca.crt -o jsonpath='{.data.ca\.crt}' | base64 -w0)
SERVER="https://127.0.0.1:6443"

TARGET="$ODOO_ROOT/manager/kubernetes"
mkdir -p "$TARGET"
chmod 700 "$TARGET"
umask 077
cat > "$TARGET/kubeconfig.json" <<JSON
{
  "apiVersion": "v1",
  "kind": "Config",
  "current-context": "sudoo",
  "clusters": [{"name": "k3s", "cluster": {"server": "$SERVER", "certificate-authority-data": "$CA"}}],
  "users": [{"name": "sudoo-panel", "user": {"token": "$TOKEN"}}],
  "contexts": [{"name": "sudoo", "context": {"cluster": "k3s", "user": "sudoo-panel"}}]
}
JSON
chmod 600 "$TARGET/kubeconfig.json"

PUBLIC=$(hostname -I 2>/dev/null | awk '{print $1}')
cat <<EOF

k3s $K3S_VERSION runs, and the panel's access is ready: $TARGET/kubeconfig.json

Next step: add these lines to the panel's .env, then restart the panel (docker compose up -d panel):

    KUBERNETES_ENGINE_BETA=true
    KUBERNETES_K3S_VERSION=$K3S_VERSION
    KUBERNETES_JOIN_URL=https://${PUBLIC:-<this server's address>}:6443

New instances can then choose the Kubernetes engine (New instance, Engine). Before you add nodes, open port 6443
(TCP) and 8472 (UDP, flannel) between the servers only, never to the internet.
EOF
