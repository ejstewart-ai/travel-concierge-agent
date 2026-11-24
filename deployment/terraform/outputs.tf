output "cloud_run_service_url" {
  description = "HTTPS endpoint of the deployed Travel Concierge Cloud Run service."
  value       = google_cloud_run_v2_service.travel_concierge.uri
}

output "runtime_service_account_email" {
  description = "Least-privilege runtime service account attached to the agent."
  value       = google_service_account.app_sa.email
}

output "cloud_sql_connection_name" {
  description = "Cloud SQL PostgreSQL connection name backing persistent ADK sessions."
  value       = google_sql_database_instance.sessions.connection_name
}

output "telemetry_bigquery_dataset" {
  description = "BigQuery dataset receiving structured AgentOps audit logs."
  value       = google_bigquery_dataset.telemetry.dataset_id
}

output "session_db_secret_id" {
  description = "Secret Manager resource name holding the Cloud SQL connection string."
  value       = google_secret_manager_secret.session_database_url.secret_id
}
