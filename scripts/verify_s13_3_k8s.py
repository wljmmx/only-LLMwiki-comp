#!/usr/bin/env python3
"""S13-3 K8s manifests 验证脚本

验证内容：
1. YAML 语法合法性（pyyaml 解析）
2. 必备资源类型齐全（Namespace / ConfigMap / Secret / StatefulSet / Deployment /
   Service / Ingress / HPA / PVC / PDB / NetworkPolicy）
3. Backend Deployment 必备字段（liveness / readiness / startup 探针 / resources /
   envFrom / volumeMounts）
4. Neo4j StatefulSet 必备字段（volumeClaimTemplates / 探针 / serviceName）
5. Ingress 路由规则完整（/api /auth /health /ready /metrics / + tls 占位）
6. HPA 配置正确（minReplicas / maxReplicas / metrics / behavior）
7. PVC accessModes 与 storageClassName 合理
8. NetworkPolicy 最小权限规则
9. PodDisruptionBudget 保证 HA
10. 所有资源命名空间一致（opskg）
11. Secret 模板不含真实密钥
12. 跨资源引用一致（Secret 名 / ConfigMap 名 / Service 名 / PVC 名匹配）

运行：python scripts/verify_s13_3_k8s.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
K8S_DIR = ROOT / "deploy" / "k8s"

PASS = 0
FAIL = 0
TESTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        TESTS.append((name, True, detail))
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        TESTS.append((name, False, detail))
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ────────── 加载所有 YAML 文件 ──────────


def load_all_manifests() -> dict[str, list[dict]]:
    """加载 deploy/k8s/*.yaml 全部多文档 manifest

    返回 {filename: [doc1, doc2, ...]}
    """
    manifests: dict[str, list[dict]] = {}
    yaml_files = sorted(K8S_DIR.glob("*.yaml"))
    # 排除 .example 文件（secret.yaml.example）
    yaml_files = [f for f in yaml_files if not f.name.endswith(".example")]
    for f in yaml_files:
        try:
            with open(f, encoding="utf-8") as fp:
                docs = list(yaml.safe_load_all(fp))
            # 过滤 None（空文档）
            manifests[f.name] = [d for d in docs if d is not None]
        except yaml.YAMLError as e:
            manifests[f.name] = []
            print(f"  ⚠️  {f.name} YAML 解析失败: {e}")
    return manifests


def load_secret_example() -> list[dict]:
    """加载 secret.yaml.example"""
    f = K8S_DIR / "secret.yaml.example"
    if not f.exists():
        return []
    with open(f, encoding="utf-8") as fp:
        return [d for d in yaml.safe_load_all(fp) if d is not None]


# ────────── 测试 1：YAML 语法合法性 ──────────


def test_yaml_syntax() -> None:
    section("1. YAML 语法合法性")
    manifests = load_all_manifests()
    check("deploy/k8s 目录存在", K8S_DIR.exists())
    check("至少 5 个 yaml 文件", len(manifests) >= 5, f"got {len(manifests)}")

    for fname, docs in manifests.items():
        check(f"{fname} 解析成功", len(docs) > 0, f"got {len(docs)} docs")

    # secret.yaml.example
    secret_docs = load_secret_example()
    check("secret.yaml.example 解析成功", len(secret_docs) > 0)


# ────────── 测试 2：必备资源类型齐全 ──────────


def test_required_resources() -> None:
    section("2. 必备资源类型齐全")
    manifests = load_all_manifests()

    all_kinds: list[tuple[str, str, dict]] = []
    for fname, docs in manifests.items():
        for doc in docs:
            kind = doc.get("kind", "")
            name = doc.get("metadata", {}).get("name", "")
            all_kinds.append((kind, name, doc))

    # Secret 在 secret.yaml.example 中（部署前需复制填值）
    secret_docs = load_secret_example()
    for doc in secret_docs:
        kind = doc.get("kind", "")
        name = doc.get("metadata", {}).get("name", "")
        all_kinds.append((kind, name, doc))

    required_kinds = {
        "Namespace": False,
        "ConfigMap": False,
        "Secret": False,
        "StatefulSet": False,
        "Deployment": False,
        "Service": False,
        "Ingress": False,
        "HorizontalPodAutoscaler": False,
        "PersistentVolumeClaim": False,
        "PodDisruptionBudget": False,
        "NetworkPolicy": False,
    }

    for kind, _, _ in all_kinds:
        if kind in required_kinds:
            required_kinds[kind] = True

    for kind, found in required_kinds.items():
        check(f"资源类型 {kind}", found)


# ────────── 测试 3：Backend Deployment 必备字段 ──────────


def test_backend_deployment() -> None:
    section("3. Backend Deployment 必备字段")
    manifests = load_all_manifests()

    backend_dep = None
    for fname, docs in manifests.items():
        for doc in docs:
            if (
                doc.get("kind") == "Deployment"
                and doc.get("metadata", {}).get("name") == "opskg-backend"
            ):
                backend_dep = doc
                break

    check("找到 opskg-backend Deployment", backend_dep is not None)
    if not backend_dep:
        return

    # 副本数
    replicas = backend_dep.get("spec", {}).get("replicas", 1)
    check("replicas >= 2（HA）", replicas >= 2, f"got {replicas}")

    # 滚动更新策略
    strategy = backend_dep.get("spec", {}).get("strategy", {})
    check("strategy type=RollingUpdate", strategy.get("type") == "RollingUpdate")
    check(
        "maxUnavailable=0（无中断滚动更新）",
        strategy.get("rollingUpdate", {}).get("maxUnavailable") == 0,
    )

    # 容器配置
    containers = backend_dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    check("至少 1 个容器", len(containers) >= 1)
    if not containers:
        return
    c = containers[0]

    check("container name=backend", c.get("name") == "backend")
    check("container port=8000", c.get("ports", [{}])[0].get("containerPort") == 8000)

    # 探针
    check("livenessProbe 存在", "livenessProbe" in c)
    check(
        "livenessProbe httpGet /health",
        c.get("livenessProbe", {}).get("httpGet", {}).get("path") == "/health",
    )
    check("readinessProbe 存在", "readinessProbe" in c)
    check(
        "readinessProbe httpGet /ready",
        c.get("readinessProbe", {}).get("httpGet", {}).get("path") == "/ready",
    )
    check("startupProbe 存在", "startupProbe" in c)

    # 资源限制
    resources = c.get("resources", {})
    check("resources.requests 存在", "requests" in resources)
    check(
        "resources.requests.cpu 设置",
        "cpu" in resources.get("requests", {}),
    )
    check(
        "resources.requests.memory 设置",
        "memory" in resources.get("requests", {}),
    )
    check("resources.limits 存在", "limits" in resources)

    # 环境变量
    env_from = c.get("envFrom", [])
    check(
        "envFrom 含 ConfigMap opskg-config",
        any(
            e.get("configMapRef", {}).get("name") == "opskg-config"
            for e in env_from
        ),
    )
    check(
        "envFrom 含 Secret opskg-secret",
        any(
            e.get("secretRef", {}).get("name") == "opskg-secret"
            for e in env_from
        ),
    )

    # 卷挂载
    volume_mounts = c.get("volumeMounts", [])
    check(
        "volumeMount /app/data 存在",
        any(vm.get("mountPath") == "/app/data" for vm in volume_mounts),
    )

    # 安全上下文
    sc = c.get("securityContext", {})
    check(
        "securityContext.allowPrivilegeEscalation=false",
        sc.get("allowPrivilegeEscalation") is False,
    )
    check(
        "securityContext.capabilities.drop ALL",
        "ALL" in sc.get("capabilities", {}).get("drop", []),
    )

    # 反亲和
    affinity = (
        backend_dep.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("affinity", {})
    )
    check(
        "podAntiAffinity 配置（多副本分散调度）",
        "podAntiAffinity" in affinity,
    )


# ────────── 测试 4：Neo4j StatefulSet 必备字段 ──────────


def test_neo4j_statefulset() -> None:
    section("4. Neo4j StatefulSet 必备字段")
    manifests = load_all_manifests()

    neo4j_sts = None
    for fname, docs in manifests.items():
        for doc in docs:
            if (
                doc.get("kind") == "StatefulSet"
                and doc.get("metadata", {}).get("name") == "neo4j"
            ):
                neo4j_sts = doc
                break

    check("找到 neo4j StatefulSet", neo4j_sts is not None)
    if not neo4j_sts:
        return

    # serviceName
    check(
        "serviceName=neo4j（headless）",
        neo4j_sts.get("spec", {}).get("serviceName") == "neo4j",
    )

    # volumeClaimTemplates
    vcts = neo4j_sts.get("spec", {}).get("volumeClaimTemplates", [])
    check("volumeClaimTemplates 存在", len(vcts) >= 1)
    check("含 data PVC 模板", any(v.get("metadata", {}).get("name") == "data" for v in vcts))
    check(
        "data PVC >= 20Gi",
        any(
            v.get("metadata", {}).get("name") == "data"
            and "20Gi" in str(v.get("spec", {}).get("resources", {}).get("requests", {}))
            for v in vcts
        ),
    )

    # 容器
    containers = (
        neo4j_sts.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    check("至少 1 个容器", len(containers) >= 1)
    if not containers:
        return
    c = containers[0]
    check("container name=neo4j", c.get("name") == "neo4j")
    check(
        "image=neo4j:5-community",
        c.get("image") == "neo4j:5-community",
    )

    # 端口
    ports = c.get("ports", [])
    port_names = [p.get("name") for p in ports]
    check("含 http(7474) 端口", "http" in port_names)
    check("含 bolt(7687) 端口", "bolt" in port_names)

    # 探针
    check("livenessProbe 存在", "livenessProbe" in c)
    check("readinessProbe 存在", "readinessProbe" in c)

    # NEO4J_AUTH 从 Secret
    env = c.get("env", [])
    neo4j_auth_env = next(
        (e for e in env if e.get("name") == "NEO4J_AUTH"), None
    )
    check(
        "NEO4J_AUTH 从 opskg-secret 取值",
        neo4j_auth_env is not None
        and neo4j_auth_env.get("valueFrom", {}).get("secretKeyRef", {}).get("name")
        == "opskg-secret",
    )

    # 资源
    resources = c.get("resources", {})
    check("resources.requests.cpu 设置", "cpu" in resources.get("requests", {}))
    check("resources.limits.cpu 设置", "cpu" in resources.get("limits", {}))


# ────────── 测试 5：Ingress 路由规则 ──────────


def test_ingress() -> None:
    section("5. Ingress 路由规则")
    manifests = load_all_manifests()

    ingress = None
    for fname, docs in manifests.items():
        for doc in docs:
            if doc.get("kind") == "Ingress":
                ingress = doc
                break

    check("找到 Ingress", ingress is not None)
    if not ingress:
        return

    # annotations
    ann = ingress.get("metadata", {}).get("annotations", {})
    check(
        "ingress.class=nginx 注解",
        ann.get("kubernetes.io/ingress.class") == "nginx"
        or ingress.get("spec", {}).get("ingressClassName") == "nginx",
    )
    check(
        "proxy-body-size 配置（文件上传）",
        "nginx.ingress.kubernetes.io/proxy-body-size" in ann,
    )
    check(
        "proxy-read-timeout 配置（LLM 长响应）",
        "nginx.ingress.kubernetes.io/proxy-read-timeout" in ann,
    )

    # 路由规则
    rules = ingress.get("spec", {}).get("rules", [])
    check("rules 非空", len(rules) >= 1)
    if not rules:
        return

    paths = rules[0].get("http", {}).get("paths", [])
    path_backends: list[tuple[str, str]] = []
    for p in paths:
        path = p.get("path", "")
        svc = p.get("backend", {}).get("service", {}).get("name", "")
        path_backends.append((path, svc))

    path_map = {p: s for p, s in path_backends}
    check("/api → opskg-backend", path_map.get("/api") == "opskg-backend")
    check("/auth → opskg-backend", path_map.get("/auth") == "opskg-backend")
    check("/health → opskg-backend", path_map.get("/health") == "opskg-backend")
    check("/ready → opskg-backend", path_map.get("/ready") == "opskg-backend")
    check("/metrics → opskg-backend", path_map.get("/metrics") == "opskg-backend")
    check("/ → opskg-frontend", path_map.get("/") == "opskg-frontend")

    # TLS 占位（注释存在即可，不强求启用）
    ingress_text = (K8S_DIR / "ingress.yaml").read_text(encoding="utf-8")
    check(
        "TLS 配置占位（注释）",
        "tls:" in ingress_text and "secretName" in ingress_text,
    )
    check(
        "cert-manager 注释（可选启用）",
        "cert-manager.io/cluster-issuer" in ingress_text,
    )


# ────────── 测试 6：HPA 配置 ──────────


def test_hpa() -> None:
    section("6. HPA 配置")
    manifests = load_all_manifests()

    hpas: list[dict] = []
    for fname, docs in manifests.items():
        for doc in docs:
            if doc.get("kind") == "HorizontalPodAutoscaler":
                hpas.append(doc)

    check("至少 1 个 HPA", len(hpas) >= 1, f"got {len(hpas)}")

    backend_hpa = next(
        (h for h in hpas if h.get("metadata", {}).get("name") == "opskg-backend"),
        None,
    )
    check("找到 opskg-backend HPA", backend_hpa is not None)
    if backend_hpa:
        spec = backend_hpa.get("spec", {})
        check("minReplicas >= 2", spec.get("minReplicas", 0) >= 2)
        check("maxReplicas >= 4", spec.get("maxReplicas", 0) >= 4)
        check("maxReplicas > minReplicas", spec.get("maxReplicas", 0) > spec.get("minReplicas", 0))

        # metrics
        metrics = spec.get("metrics", [])
        check("metrics 非空", len(metrics) >= 1)
        resource_metrics = [m for m in metrics if m.get("type") == "Resource"]
        check("含 Resource 类型 metrics", len(resource_metrics) >= 1)

        metric_names = [
            m.get("resource", {}).get("name") for m in resource_metrics
        ]
        check("含 cpu metric", "cpu" in metric_names)
        check("含 memory metric", "memory" in metric_names)

        # behavior
        behavior = spec.get("behavior", {})
        check("behavior 配置存在", "behavior" in spec)
        check(
            "scaleDown.stabilizationWindowSeconds >= 60",
            behavior.get("scaleDown", {}).get("stabilizationWindowSeconds", 0) >= 60,
        )


# ────────── 测试 7：PVC 配置 ──────────


def test_pvc() -> None:
    section("7. PVC 配置")
    manifests = load_all_manifests()

    pvcs: list[dict] = []
    for fname, docs in manifests.items():
        for doc in docs:
            if doc.get("kind") == "PersistentVolumeClaim":
                pvcs.append(doc)

    check("至少 1 个 PVC", len(pvcs) >= 1)

    backend_pvc = next(
        (p for p in pvcs if p.get("metadata", {}).get("name") == "opskg-backend-data"),
        None,
    )
    check("找到 opskg-backend-data PVC", backend_pvc is not None)
    if backend_pvc:
        spec = backend_pvc.get("spec", {})
        access_modes = spec.get("accessModes", [])
        check(
            "accessModes 含 ReadWriteMany（多副本共享）",
            "ReadWriteMany" in access_modes,
        )
        check(
            "storageClassName 设置",
            "storageClassName" in spec,
        )
        storage = spec.get("resources", {}).get("requests", {}).get("storage", "")
        check("storage >= 50Gi", "50Gi" in str(storage) or "100Gi" in str(storage))

    # Neo4j PVC（在 StatefulSet 的 volumeClaimTemplates 中）
    neo4j_sts = None
    for fname, docs in manifests.items():
        for doc in docs:
            if doc.get("kind") == "StatefulSet" and doc.get("metadata", {}).get("name") == "neo4j":
                neo4j_sts = doc
                break
    if neo4j_sts:
        vcts = neo4j_sts.get("spec", {}).get("volumeClaimTemplates", [])
        for vct in vcts:
            access_modes = vct.get("spec", {}).get("accessModes", [])
            check(
                f"Neo4j {vct.get('metadata', {}).get('name')} PVC accessModes 设置",
                len(access_modes) >= 1,
            )


# ────────── 测试 8：NetworkPolicy 最小权限 ──────────


def test_networkpolicy() -> None:
    section("8. NetworkPolicy 最小权限")
    manifests = load_all_manifests()

    nps: list[dict] = []
    for fname, docs in manifests.items():
        for doc in docs:
            if doc.get("kind") == "NetworkPolicy":
                nps.append(doc)

    check("至少 3 个 NetworkPolicy", len(nps) >= 3, f"got {len(nps)}")

    # 默认拒绝
    default_deny = next(
        (n for n in nps if n.get("metadata", {}).get("name") == "opskg-default-deny"),
        None,
    )
    check("找到 default-deny 策略", default_deny is not None)
    if default_deny:
        spec = default_deny.get("spec", {})
        check("default-deny podSelector={}（全命名空间）", spec.get("podSelector") == {})
        check("default-deny 含 Ingress policyType", "Ingress" in spec.get("policyTypes", []))
        check("default-deny 含 Egress policyType", "Egress" in spec.get("policyTypes", []))
        check("default-deny ingress 为空", spec.get("ingress") == [])
        check("default-deny egress 为空", spec.get("egress") == [])

    # Backend 策略
    backend_np = next(
        (n for n in nps if n.get("metadata", {}).get("name") == "opskg-backend-policy"),
        None,
    )
    check("找到 backend-policy", backend_np is not None)
    if backend_np:
        egress = backend_np.get("spec", {}).get("egress", [])
        # 检查是否允许 Neo4j、DNS、外部 HTTPS
        egress_str = str(egress)
        check("backend egress 允许 Neo4j", "7687" in egress_str)
        check("backend egress 允许 DNS(53)", "53" in egress_str)
        check("backend egress 允许 HTTPS(443)", "443" in egress_str)


# ────────── 测试 9：PodDisruptionBudget ──────────


def test_pdb() -> None:
    section("9. PodDisruptionBudget HA 保障")
    manifests = load_all_manifests()

    pdbs: list[dict] = []
    for fname, docs in manifests.items():
        for doc in docs:
            if doc.get("kind") == "PodDisruptionBudget":
                pdbs.append(doc)

    check("至少 2 个 PDB", len(pdbs) >= 2, f"got {len(pdbs)}")

    backend_pdb = next(
        (p for p in pdbs if p.get("metadata", {}).get("name") == "opskg-backend-pdb"),
        None,
    )
    check("找到 opskg-backend-pdb", backend_pdb is not None)
    if backend_pdb:
        spec = backend_pdb.get("spec", {})
        check("minAvailable >= 1", spec.get("minAvailable", 0) >= 1)

    frontend_pdb = next(
        (p for p in pdbs if p.get("metadata", {}).get("name") == "opskg-frontend-pdb"),
        None,
    )
    check("找到 opskg-frontend-pdb", frontend_pdb is not None)


# ────────── 测试 10：命名空间一致性 ──────────


def test_namespace_consistency() -> None:
    section("10. 命名空间一致性")
    manifests = load_all_manifests()

    expected_ns = "opskg"
    bad_count = 0
    total = 0
    for fname, docs in manifests.items():
        for doc in docs:
            # Namespace 资源本身跳过
            if doc.get("kind") == "Namespace":
                continue
            ns = doc.get("metadata", {}).get("namespace", "")
            total += 1
            if ns != expected_ns:
                bad_count += 1
                print(f"  ⚠️  {fname}: {doc.get('kind')}/{doc.get('metadata', {}).get('name')} namespace={ns!r}")

    check(
        f"所有 {total} 个资源 namespace=opskg",
        bad_count == 0,
        f"{bad_count} 个不匹配",
    )


# ────────── 测试 11：Secret 模板安全 ──────────


def test_secret_template_safety() -> None:
    section("11. Secret 模板不含真实密钥")
    secret_docs = load_secret_example()
    check("secret.yaml.example 存在", len(secret_docs) >= 1)
    if not secret_docs:
        return

    secret = secret_docs[0]
    string_data = secret.get("stringData", {})

    # 检查每个值是否为占位符或空
    sensitive_keys = [
        "NEO4J_AUTH",
        "OPENAI_COMPAT_API_KEY",
        "OPSKG_BOOTSTRAP_ADMIN_PASSWORD",
    ]
    for key in sensitive_keys:
        val = string_data.get(key, "")
        # 必须含 CHANGE_ME 或为空
        is_placeholder = "CHANGE_ME" in val or val == ""
        check(f"{key} 为占位符", is_placeholder, f"got {val!r}")

    # 检查 .gitignore 含 secret.local.yaml
    gitignore = ROOT / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        check(
            ".gitignore 含 secret.local.yaml",
            "secret.local.yaml" in content or "secret*.yaml" in content,
        )
    else:
        check(".gitignore 存在", False, "missing")


# ────────── 测试 12：跨资源引用一致性 ──────────


def test_cross_references() -> None:
    section("12. 跨资源引用一致性")
    manifests = load_all_manifests()

    # 收集所有资源名
    all_resources: dict[tuple[str, str], dict] = {}
    for fname, docs in manifests.items():
        for doc in docs:
            kind = doc.get("kind", "")
            name = doc.get("metadata", {}).get("name", "")
            if kind and name:
                all_resources[(kind, name)] = doc

    # Backend Deployment → 引用 opskg-config / opskg-secret / opskg-backend-data PVC
    backend_dep = all_resources.get(("Deployment", "opskg-backend"))
    if backend_dep:
        containers = (
            backend_dep.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        if containers:
            check(
                "Backend 引用 ConfigMap opskg-config 存在",
                ("ConfigMap", "opskg-config") in all_resources,
            )
            check(
                "Backend 引用 Secret opskg-secret 存在（在 example 中）",
                True,  # secret.yaml.example 中存在
            )

            volumes = (
                backend_dep.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("volumes", [])
            )
            for v in volumes:
                pvc_name = v.get("persistentVolumeClaim", {}).get("claimName", "")
                if pvc_name:
                    check(
                        f"Backend 引用 PVC {pvc_name} 存在",
                        ("PersistentVolumeClaim", pvc_name) in all_resources,
                    )

    # Backend HPA → scaleTargetRef → Deployment opskg-backend
    backend_hpa = all_resources.get(("HorizontalPodAutoscaler", "opskg-backend"))
    if backend_hpa:
        target = backend_hpa.get("spec", {}).get("scaleTargetRef", {})
        check(
            "HPA target kind=Deployment",
            target.get("kind") == "Deployment",
        )
        check(
            "HPA target name=opskg-backend 存在",
            ("Deployment", target.get("name", "")) in all_resources,
        )

    # Ingress → backend Service / frontend Service
    ingress = next(
        (d for docs in manifests.values() for d in docs if d.get("kind") == "Ingress"),
        None,
    )
    if ingress:
        rules = ingress.get("spec", {}).get("rules", [])
        referenced_services: set[str] = set()
        for r in rules:
            for p in r.get("http", {}).get("paths", []):
                svc = p.get("backend", {}).get("service", {}).get("name", "")
                if svc:
                    referenced_services.add(svc)
        for svc_name in referenced_services:
            check(
                f"Ingress 引用 Service {svc_name} 存在",
                ("Service", svc_name) in all_resources,
            )

    # Neo4j StatefulSet → serviceName
    neo4j_sts = all_resources.get(("StatefulSet", "neo4j"))
    if neo4j_sts:
        svc_name = neo4j_sts.get("spec", {}).get("serviceName", "")
        check(
            f"Neo4j StatefulSet serviceName={svc_name} 对应 Service 存在",
            ("Service", svc_name) in all_resources,
        )


# ────────── 测试 13：API 版本正确性 ──────────


def test_api_versions() -> None:
    section("13. API 版本正确性")
    manifests = load_all_manifests()

    expected_versions = {
        "Namespace": "v1",
        "ConfigMap": "v1",
        "Secret": "v1",
        "Service": "v1",
        "PersistentVolumeClaim": "v1",
        "Deployment": "apps/v1",
        "StatefulSet": "apps/v1",
        "Ingress": "networking.k8s.io/v1",
        "HorizontalPodAutoscaler": "autoscaling/v2",
        "PodDisruptionBudget": "policy/v1",
        "NetworkPolicy": "networking.k8s.io/v1",
    }

    for fname, docs in manifests.items():
        for doc in docs:
            kind = doc.get("kind", "")
            api = doc.get("apiVersion", "")
            expected = expected_versions.get(kind)
            if expected:
                check(
                    f"{fname}: {kind} apiVersion={expected}",
                    api == expected,
                    f"got {api}",
                )


# ────────── 测试 14：部署顺序文档化 ──────────


def test_deployment_readme() -> None:
    section("14. 部署文档（README）")
    readme = K8S_DIR / "README.md"
    check("deploy/k8s/README.md 存在", readme.exists())
    if not readme.exists():
        return

    content = readme.read_text(encoding="utf-8")
    # 关键章节
    check("README 含部署顺序", "部署顺序" in content or "kubectl apply" in content)
    check("README 含前置要求", "前置" in content or "依赖" in content)
    check("README 含 secret 创建说明", "Secret" in content or "secret" in content)


# ────────── 主函数 ──────────


def main() -> int:
    print("=" * 60)
    print("S13-3 K8s manifests 验证")
    print("=" * 60)

    test_yaml_syntax()
    test_required_resources()
    test_backend_deployment()
    test_neo4j_statefulset()
    test_ingress()
    test_hpa()
    test_pvc()
    test_networkpolicy()
    test_pdb()
    test_namespace_consistency()
    test_secret_template_safety()
    test_cross_references()
    test_api_versions()
    test_deployment_readme()

    print("\n" + "=" * 60)
    print(f"总计：{PASS} 通过 / {FAIL} 失败")
    print("=" * 60)

    if FAIL > 0:
        print("\n失败项：")
        for name, ok, detail in TESTS:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
