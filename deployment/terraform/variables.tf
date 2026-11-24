variable "project_id" {
  description = "Google Cloud project ID where the Travel Concierge Agent will be provisioned."
  type        = string
}

variable "region" {
  description = "Primary Google Cloud region for Cloud Run, Cloud SQL, and Vertex AI."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name and resource prefix."
  type        = string
  default     = "travel-concierge-agent"
}

variable "container_image" {
  description = "Artifact Registry container image URI built from the root Dockerfile."
  type        = string
}

variable "fast_model" {
  description = "Fast-tier Gemini model used for coordination, parallel research, and compaction."
  type        = string
  default     = "gemini-2.5-flash"
}

variable "reasoning_model" {
  description = "Reasoning-tier Gemini model used for itinerary synthesis and self-evaluation."
  type        = string
  default     = "gemini-2.5-pro"
}

variable "cloud_sql_tier" {
  description = "Cloud SQL PostgreSQL machine tier for persistent ADK sessions."
  type        = string
  default     = "db-f1-micro"
}
