resource "azurerm_kubernetes_cluster" "pctasks" {
  name                = "aks-${local.prefix}"
  location            = azurerm_resource_group.pctasks.location
  resource_group_name = azurerm_resource_group.pctasks.name
  dns_prefix          = "${local.prefix}-cluster"
  kubernetes_version  = var.k8s_version



  default_node_pool {
    name                 = "agentpool"
    vm_size              = "Standard_DS2_v2"
    node_count           = var.aks_node_count
    vnet_subnet_id       = azurerm_subnet.k8snode_subnet.id

    node_labels = {
      node_group = "default"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  azure_active_directory_role_based_access_control {
    managed            = true
    azure_rbac_enabled = true
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "AI4E"
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "argowf" {
  name                  = "argowf"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.pctasks.id
  vm_size               = "Standard_DS2_v2"
  node_count            = 1

  node_labels = {
    node_group = var.argo_wf_node_group_name
  }

   lifecycle {
    ignore_changes = [
      # Ignore changes that are auto-populated by AKS
      vnet_subnet_id,
      node_taints,
      zones,
    ]
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "AI4E"
  }
}

# add the role to the identity the kubernetes cluster was assigned
resource "azurerm_role_assignment" "network" {
  scope                = azurerm_resource_group.pctasks.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_kubernetes_cluster.pctasks.identity[0].principal_id
}