resource "google_cloud_run_v2_service" "travel_concierge" {
  project  = var.project_id
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.app_sa.email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.sessions.connection_name]
      }
    }

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "ENVIRONMENT"
        value = "production"
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "TRUE"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "TRAVEL_FAST_MODEL"
        value = var.fast_model
      }
      env {
        name  = "TRAVEL_REASONING_MODEL"
        value = var.reasoning_model
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = var.service_name
      }
      env {
        name  = "OTEL_EXPORTER_MODE"
        value = "cloud_trace"
      }
      env {
        name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
        value = "NO_CONTENT"
      }
      env {
        name  = "ENABLE_CLOUD_DLP"
        value = "true"
      }
      env {
        name  = "USE_SECRET_MANAGER"
        value = "true"
      }
      env {
        name  = "LOGS_BUCKET_NAME"
        value = google_storage_bucket.telemetry_logs.name
      }
      env {
        name = "SESSION_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.session_database_url.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 6
      }
    }
  }

  depends_on = [
    google_project_iam_member.app_sa_roles,
    google_secret_manager_secret_version.session_database_url_initial,
    google_secret_manager_secret_iam_member.session_db_url_accessor,
    google_sql_database.adk_sessions,
    google_sql_user.agent_app,
  ]
}
