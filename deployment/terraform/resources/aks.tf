resource "azurerm_kubernetes_cluster" "rxetl" {
  name                = "${local.prefix}-cluster"
  location            = azurerm_resource_group.rxetl.location
  resource_group_name = azurerm_resource_group.rxetl.name
  dns_prefix          = "${local.prefix}-cluster"
  kubernetes_version  = var.k8s_version

  addon_profile {
    kube_dashboard {
      enabled = false
    }
  }

  default_node_pool {
    name           = "agentpool"
    vm_size        = "Standard_DS2_v2"
    node_count     = var.aks_node_count
    vnet_subnet_id = azurerm_subnet.k8snode_subnet.id
  }

  identity {
    type = "SystemAssigned"
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "AI4E"
  }
}

# add the role to the identity the kubernetes cluster was assigned
resource "azurerm_role_assignment" "network" {
  scope                = azurerm_resource_group.rxetl.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_kubernetes_cluster.rxetl.identity[0].principal_id
}