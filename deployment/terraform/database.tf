resource "random_id" "db_suffix" {
  byte_length = 4
}

resource "random_password" "session_db_password" {
  length  = 24
  special = false
}

resource "google_sql_database_instance" "sessions" {
  project             = var.project_id
  name                = "${var.service_name}-sessions-${random_id.db_suffix.hex}"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = false

  settings {
    tier = var.cloud_sql_tier

    backup_configuration {
      enabled = true
    }
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_sql_database" "adk_sessions" {
  project  = var.project_id
  name     = "adk_sessions"
  instance = google_sql_database_instance.sessions.name
}

resource "google_sql_user" "agent_app" {
  project  = var.project_id
  name     = "agent_app"
  instance = google_sql_database_instance.sessions.name
  password = random_password.session_db_password.result
}
