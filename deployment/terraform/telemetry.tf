locals {
  telemetry_dataset_id = "${replace(var.service_name, "-", "_")}_telemetry"
}

resource "google_storage_bucket" "telemetry_logs" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.service_name}-logs"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_storage_bucket_iam_member" "app_sa_logs_writer" {
  bucket = google_storage_bucket.telemetry_logs.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_bigquery_dataset" "telemetry" {
  project                    = var.project_id
  dataset_id                 = local.telemetry_dataset_id
  location                   = var.region
  description                = "Structured AgentOps audit and intent-vs-outcome telemetry for Travel Concierge Agent."
  delete_contents_on_destroy = true

  depends_on = [google_project_service.enabled_apis]
}

resource "google_logging_project_sink" "agent_audit_sink" {
  project                = var.project_id
  name                   = "${var.service_name}-audit-bq-sink"
  destination            = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${google_bigquery_dataset.telemetry.dataset_id}"
  filter                 = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.service_name}\""
  unique_writer_identity = true

  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "log_sink_bq_writer" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.telemetry.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.agent_audit_sink.writer_identity
}
