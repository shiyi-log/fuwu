from django.urls import path, re_path

from . import views

urlpatterns = [
    path("healthz", views.api_view),
    path("api/auth/login", views.api_view),
    path("api/auth/codes", views.api_view),
    path("api/auth/logout", views.api_view),
    path("api/auth/password", views.api_view),
    path("api/user/info", views.api_view),
    path("api/users", views.api_view),
    path("api/users/<str:user_id>", views.api_view),
    path("api/users/<str:user_id>/password", views.api_view),
    path("api/roles", views.api_view),
    path("api/projects", views.api_view),
    path("api/projects/<str:project_id>", views.api_view),
    path("api/releases", views.api_view),
    re_path(r"^api/downloads/(?P<project_id>[^/]+)/(?P<channel>stable|beta)/latest\.json$", views.latest_download_view),
    path("api/downloads/<str:project_id>/<str:version>/<str:filename>", views.download_view),
    path("releases/<path:storage_key>", views.storage_key_view),
    path("api/release-storage/status", views.api_view),
    path("api/release-storage/latest", views.api_view),
    path("api/release-storage/stage-remote", views.api_view),
    path("api/release-storage/publish", views.api_view),
    path("api/release-storage/rollback", views.api_view),
    path("api/release-storage/audit", views.api_view),
    path("api/logs", views.api_view),
    path("api/settings", views.api_view),
    path("api/settings/storage", views.api_view),
    path("api/settings/github-authorization", views.api_view),
]
