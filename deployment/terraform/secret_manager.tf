# Provision Secret Manager containers without embedding plaintext credentials in IaC.
# Operators populate secret versions out-of-band via `gcloud secrets versions add`.

resource "google_secret_manager_secret" "session_database_url" {
  project   = var.project_id
  secret_id = "${var.service_name}-session-db-url"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_secret_manager_secret_version" "session_database_url_initial" {
  secret      = google_secret_manager_secret.session_database_url.id
  secret_data = "postgresql+asyncpg://agent_app:${random_password.session_db_password.result}@/adk_sessions?host=/cloudsql/${google_sql_database_instance.sessions.connection_name}"
}

resource "google_secret_manager_secret" "optional_api_key" {
  project   = var.project_id
  secret_id = "${var.service_name}-google-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_secret_manager_secret_iam_member" "session_db_url_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.session_database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "api_key_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.optional_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app_sa.email}"
}
