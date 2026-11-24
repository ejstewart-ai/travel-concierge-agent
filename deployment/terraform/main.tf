locals {
  required_apis = [
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudtrace.googleapis.com",
    "dlp.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
  ]

  runtime_sa_roles = [
    "roles/aiplatform.user",
    "roles/cloudsql.client",
    "roles/cloudtrace.agent",
    "roles/dlp.user",
    "roles/logging.logWriter",
    "roles/secretmanager.secretAccessor",
  ]
}

resource "google_project_service" "enabled_apis" {
  for_each = toset(local.required_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "agent_repo" {
  project       = var.project_id
  location      = var.region
  repository_id = var.service_name
  description   = "Container repository for Travel Concierge ADK Agent images."
  format        = "DOCKER"

  depends_on = [google_project_service.enabled_apis]
}

resource "google_service_account" "app_sa" {
  project      = var.project_id
  account_id   = "${var.service_name}-sa"
  display_name = "Travel Concierge Agent Runtime Service Account"

  depends_on = [google_project_service.enabled_apis]
}

resource "google_project_iam_member" "app_sa_roles" {
  for_each = toset(local.runtime_sa_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}
