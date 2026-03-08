from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("icea_core.urls")),
    path("api/v1/", include("icea_pipeline.urls")),
]
