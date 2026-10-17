from django.urls import path
from routing.views import analyze_route, health_check

urlpatterns = [
    path("", health_check, name="root-health"),
    path("api/health/", health_check, name="health-check"),
    path("api/analyze/", analyze_route, name="analyze-route"),
]
