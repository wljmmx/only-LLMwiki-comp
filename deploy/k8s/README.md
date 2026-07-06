# OpsKG Kubernetes 部署清单（S13-3）

OpsKG 在 Kubernetes 上的生产级部署清单。所有资源统一放置到 `opskg` 命名空间。

## 资源清单

| 文件 | 资源 | 说明 |
|------|------|------|
| `namespace.yaml` | Namespace | opskg 命名空间 |
| `configmap.yaml` | ConfigMap | 非敏感配置（LLM / Neo4j / 抽取门控等） |
| `secret.yaml.example` | Secret 模板 | 敏感配置模板，**部署前需替换为真实值** |
| `neo4j-statefulset.yaml` | StatefulSet + Service | Neo4j 5.x 图数据库，含 PVC + 探针 |
| `backend-deployment.yaml` | Deployment + Service | 后端 FastAPI，含 liveness/readiness/startup 探针 |
| `backend-pvc.yaml` | PersistentVolumeClaim | 后端 SQLite 数据持久化 |
| `backend-hpa.yaml` | HorizontalPodAutoscaler | 后端 CPU/内存自动扩缩容 |
| `frontend-deployment.yaml` | Deployment + Service + HPA | 前端 nginx 静态资源（镜像在 S13-4 构建） |
| `ingress.yaml` | Ingress | 路由 `/api` `/auth` → backend，`/` → frontend |
| `networkpolicy.yaml` | NetworkPolicy ×4 | 最小权限网络隔离（默认拒绝 + 各 Pod 精细规则） |
| `poddisruptionbudget.yaml` | PDB ×2 | 自愿驱逐时保证最少可用副本 |

## 前置要求

1. **Kubernetes 集群** ≥ 1.24
2. **Ingress controller**（nginx-ingress 或兼容）
3. **metrics-server**（HPA 必需，`kubectl top pod` 可用）
4. **StorageClass**：
   - 单实例：`standard` / `gp2`（ReadWriteOnce）
   - 多副本 HA：`nfs-client` / `cephfs` / `azurefile`（ReadWriteMany）
5. **CNI 支持 NetworkPolicy**：Calico / Cilium / Weave Net（flannel 默认不支持，本清单会被忽略但不会报错）
6. **DNS 解析**：将公网域名（如 `opskg.example.com`）指向 Ingress controller 外网 IP
7. **TLS 证书**（推荐 cert-manager 自动签发 Let's Encrypt）

## 部署顺序

### 1. 创建命名空间

```bash
kubectl apply -f deploy/k8s/namespace.yaml
```

### 2. 创建 Secret

**不要直接 apply `secret.yaml.example`**，使用以下任一方式：

```bash
# 方式 1：kubectl create（推荐）
kubectl create secret generic opskg-secret -n opskg \
  --from-literal=NEO4J_AUTH='neo4j/your-real-password' \
  --from-literal=OPENAI_COMPAT_API_KEY='sk-xxx' \
  --from-literal=OPSKG_API_TOKEN='your-api-token' \
  --from-literal=OPSKG_BOOTSTRAP_ADMIN_USER='admin' \
  --from-literal=OPSKG_BOOTSTRAP_ADMIN_PASSWORD='change-me-in-production' \
  --from-literal=OIDC_PROVIDERS='' \
  --from-literal=SAML_PROVIDERS='' \
  --from-literal=LDAP_PROVIDERS=''

# 方式 2：复制 example 后填值再 apply（适合 SAML 证书等长文本）
cp deploy/k8s/secret.yaml.example deploy/k8s/secret.local.yaml
# 编辑 secret.local.yaml 填入真实值
kubectl apply -f deploy/k8s/secret.local.yaml
rm deploy/k8s/secret.local.yaml  # 部署后删除本地文件
```

### 3. 创建 ConfigMap

```bash
# 编辑 deploy/k8s/configmap.yaml 调整 LLM 模型 / 前端域名等
kubectl apply -f deploy/k8s/configmap.yaml
```

### 4. 创建 PVC

```bash
# 编辑 deploy/k8s/backend-pvc.yaml 选择合适的 storageClassName
kubectl apply -f deploy/k8s/backend-pvc.yaml
```

### 5. 部署 Neo4j

```bash
kubectl apply -f deploy/k8s/neo4j-statefulset.yaml

# 等待就绪
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=neo4j -n opskg --timeout=300s
```

### 6. 构建并推送后端镜像

```bash
# 在仓库根目录
docker build -t opskg-backend:latest .
# 推送到镜像仓库（生产推荐）
# docker tag opskg-backend:latest ghcr.io/your-org/opskg-backend:v0.1.0
# docker push ghcr.io/your-org/opskg-backend:v0.1.0
```

### 7. 部署后端

```bash
kubectl apply -f deploy/k8s/backend-deployment.yaml
kubectl apply -f deploy/k8s/backend-hpa.yaml
kubectl apply -f deploy/k8s/poddisruptionbudget.yaml

# 等待就绪
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=opskg-backend -n opskg --timeout=180s
```

### 8. 构建前端镜像（S13-4 后）

```bash
# S13-4 将提供多阶段 Dockerfile，构建为 opskg-frontend:latest
# docker build -f Dockerfile.frontend -t opskg-frontend:latest .
```

### 9. 部署前端

```bash
kubectl apply -f deploy/k8s/frontend-deployment.yaml
```

### 10. 部署 Ingress

```bash
# 编辑 deploy/k8s/ingress.yaml 替换 opskg.example.com 为实际域名
# 如需启用 HTTPS，取消 tls 段注释 + 安装 cert-manager
kubectl apply -f deploy/k8s/ingress.yaml
```

### 11. （可选）部署 NetworkPolicy

```bash
kubectl apply -f deploy/k8s/networkpolicy.yaml
```

## 验证部署

```bash
# 查看所有资源
kubectl get all -n opskg

# 查看后端日志
kubectl logs -f -l app.kubernetes.io/name=opskg-backend -n opskg

# 测试健康检查
kubectl port-forward svc/opskg-backend 8000:8000 -n opskg
curl http://localhost:8000/health
curl http://localhost:8000/ready

# 测试 Ingress（替换为实际域名）
curl https://opskg.example.com/health
curl https://opskg.example.com/ready
```

## 配置变更

修改 `configmap.yaml` 后滚动更新：

```bash
kubectl apply -f deploy/k8s/configmap.yaml
kubectl rollout restart deployment/opskg-backend -n opskg
kubectl rollout status deployment/opskg-backend -n opskg
```

修改 Secret（如轮换 API Key）：

```bash
kubectl delete secret opskg-secret -n opskg
kubectl create secret generic opskg-secret -n opskg \
  --from-literal=NEO4J_AUTH='neo4j/new-password' \
  ...
kubectl rollout restart deployment/opskg-backend -n opskg
```

## 监控

```bash
# HPA 状态
kubectl get hpa -n opskg

# Pod 资源使用
kubectl top pod -n opskg

# Prometheus 抓取
kubectl port-forward svc/opskg-backend 8000:8000 -n opskg
curl http://localhost:8000/metrics
```

## 故障排查

### Pod 无法启动

```bash
kubectl describe pod <pod-name> -n opskg
kubectl logs <pod-name> -n opskg
```

### Readiness 探针失败

```bash
# 检查依赖状态
kubectl exec -it <pod-name> -n opskg -- curl http://localhost:8000/ready
```

### PVC Pending

```bash
kubectl get pvc -n opskg
kubectl describe pvc opskg-backend-data -n opskg
# 检查 StorageClass 是否存在 + 是否支持所需 accessMode
kubectl get storageclass
```

### Ingress 502

```bash
# 检查 Endpoints 是否就绪
kubectl get endpoints -n opskg
# 检查 Ingress 事件
kubectl describe ingress opskg-ingress -n opskg
```

## 生产加固清单

- [ ] 替换所有 `CHANGE_ME` 占位为真实强密码
- [ ] 启用 TLS（cert-manager + Let's Encrypt）
- [ ] 配置 ReadWriteMany 存储（多副本 HA）
- [ ] 启用 NetworkPolicy
- [ ] 配置备份（Velero / k8up 定期备份 PVC）
- [ ] 配置监控告警（Prometheus + AlertManager）
- [ ] 配置日志聚合（Loki / ELK）
- [ ] 配置资源配额（ResourceQuota + LimitRange）
- [ ] 配置 RBAC（ServiceAccount + RoleBinding）
- [ ] 配置镜像扫描（Trivy / Snyk）
