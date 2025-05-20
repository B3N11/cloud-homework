import kopf
import kubernetes.client as k8s_client
from kubernetes.client.rest import ApiException

INGRESS_CLASS_ANNOTATION = "traefik.ingress.kubernetes.io/router.entrypoints"
INGRESS_CLASS_VALUE = "web"
AUTO_INGRESS_ANNOTATION = "auto-ingress"


def build_ingress_name(service_name, port_name):
    return f"auto-ingress-{service_name}-{port_name}"


def find_http_port(service):
    ports = service.spec['ports']
    for port in ports:
        if port['name'] == "http":
            return port
    return ports[0] if ports else None


def create_ingress_object(namespace, service_name, path, port):
    ingress_name = build_ingress_name(service_name, port['name'] or str(port['port']))

    return k8s_client.V1Ingress(
        metadata=k8s_client.V1ObjectMeta(
            name=ingress_name,
            namespace=namespace,
            annotations={INGRESS_CLASS_ANNOTATION: INGRESS_CLASS_VALUE},
            owner_references=[
                k8s_client.V1OwnerReference(
                    api_version="v1",
                    kind="Service",
                    name=service_name,
                    uid="",
                    controller=True,
                    block_owner_deletion=True,
                )
            ]
        ),
        spec=k8s_client.V1IngressSpec(
            rules=[
                k8s_client.V1IngressRule(
                    http=k8s_client.V1HTTPIngressRuleValue(
                        paths=[
                            k8s_client.V1HTTPIngressPath(
                                path=path,
                                path_type="Prefix",
                                backend=k8s_client.V1IngressBackend(
                                    service=k8s_client.V1IngressServiceBackend(
                                        name=service_name,
                                        port=k8s_client.V1ServiceBackendPort(
                                            number=port['targetPort'] or port['port']
                                        ),
                                    )
                                ),
                            )
                        ]
                    )
                )
            ]
        )
    )


@kopf.on.create('v1', 'services')
@kopf.on.update('v1', 'services')
def manage_ingress(spec, meta, status, name, namespace, uid, annotations, **_):
    api = k8s_client.NetworkingV1Api()
    core = k8s_client.CoreV1Api()

    ingress_name_prefix = f"auto-ingress-{name}"

    # Clean up existing auto-ingress if annotation was removed
    if AUTO_INGRESS_ANNOTATION not in (annotations or {}):
        try:
            ingresses = api.list_namespaced_ingress(namespace).items
            for ing in ingresses:
                if ing.metadata.name.startswith(ingress_name_prefix):
                    api.delete_namespaced_ingress(ing.metadata.name, namespace)
                    kopf.info(meta, reason="IngressDeleted", message=f"Deleted {ing.metadata.name}")
        except ApiException as e:
            raise kopf.TemporaryError(f"Failed to list/delete ingress: {e}", delay=10)
        return

    path = annotations[AUTO_INGRESS_ANNOTATION]
    port = find_http_port(k8s_client.V1Service(**{'spec': spec}))

    if not port:
        raise kopf.TemporaryError("No ports found on service", delay=10)

    ingress_obj = create_ingress_object(namespace, name, path, port)
    ingress_obj.metadata.owner_references[0].uid = uid

    ingress_name = ingress_obj.metadata.name

    try:
        api.create_namespaced_ingress(namespace, ingress_obj)
        kopf.info(meta, reason="IngressCreated", message=f"Created ingress {ingress_name}")
    except ApiException as e:
        if e.status == 409:
            # Already exists, try patching
            api.patch_namespaced_ingress(ingress_name, namespace, ingress_obj)
            kopf.info(meta, reason="IngressPatched", message=f"Updated ingress {ingress_name}")
        else:
            raise kopf.TemporaryError(f"Failed to create/patch ingress: {e}", delay=10)


@kopf.on.delete('v1', 'services')
def cleanup_ingress(name, namespace, **_):
    print(f"Cleaning up ingress for service {name} in namespace {namespace}!!!!!!!!!!!!!!!!!!")
    api = k8s_client.NetworkingV1Api()
    ingress_name_prefix = f"auto-ingress-{name}"
    ingresses = api.list_namespaced_ingress(namespace).items
    for ing in ingresses:
        if ing.metadata.name.startswith(ingress_name_prefix):
            api.delete_namespaced_ingress(ing.metadata.name, namespace)
            kopf.info(ing.metadata, reason="IngressDeleted", message=f"Deleted ingress {ing.metadata.name}")
