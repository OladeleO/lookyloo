   #Example 'main.tf':
     # The configuration for the `remote` backend.
#     terraform {
#       backend "remote" {
#         # The name of your Terraform Cloud organization.
#         organization = "example-organization"

#         # The name of the Terraform Cloud workspace to store Terraform state files in.
#         workspaces {
#           name = "example-workspace"
#         }
#       }
#     }


provider "google" {
   version = "3.53"
   
   
   project = var.project
   region  = var.region
   zone    = var.region
}

resource "google_container_cluster" "my_vpc_native_cluster" {
   name                 = var.gke_cluster_name
   location             = var.zone
   initial_node_count   = 1
   
   network              = "default"
   subnetwork           = "default"
  
}
