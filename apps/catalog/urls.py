from rest_framework.routers import DefaultRouter

from apps.catalog.views import (
    CentreTestViewSet,
    DiagnosticCentreViewSet,
    DiagnosticTestViewSet,
)

app_name = "catalog"

router = DefaultRouter()
router.register("centres", DiagnosticCentreViewSet, basename="centre")
router.register("tests", DiagnosticTestViewSet, basename="test")
router.register("offerings", CentreTestViewSet, basename="offering")

urlpatterns = router.urls
