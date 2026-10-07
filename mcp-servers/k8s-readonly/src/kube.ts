import { AppsV1Api, CoreV1Api, KubeConfig } from "@kubernetes/client-node"

import type { Kube } from "./tools.ts"

/**
 * The real cluster: the in-cluster service account when running in a pod
 * (its Role allows get/list/watch only), else the local kubeconfig.
 */
export function clusterKube(): Kube {
  const config = new KubeConfig()
  config.loadFromDefault()
  const apps = config.makeApiClient(AppsV1Api)
  const core = config.makeApiClient(CoreV1Api)
  return {
    deployments: async (namespace) => (await apps.listNamespacedDeployment({ namespace })).items,
    replicaSets: async (namespace) => (await apps.listNamespacedReplicaSet({ namespace })).items,
    pods: async (namespace) => (await core.listNamespacedPod({ namespace })).items,
    events: async (namespace) => (await core.listNamespacedEvent({ namespace })).items,
  }
}
