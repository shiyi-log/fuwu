from django.urls import include, path

urlpatterns = [
    path("", include("release_api.urls")),
]

