from django.urls import path

from . import views

urlpatterns = [
    path("healthz", views.healthz),
    path("api/projects", views.projects),
    path("api/releases", views.releases),
    path("api/release-storage/status", views.status),
    path("api/release-storage/publish", views.publish),
    path("api/release-storage/rollback", views.rollback),
    path("api/release-storage/audit", views.audit),
]
